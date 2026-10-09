"""
scraper.py — Production-grade web scraper for Egyptian Laws & Decrees.

Architecture
────────────
  ScraperSession   : manages a shared httpx.Client with retry / UA rotation
  LawIndexScraper  : paginates through the law listing pages, yields LawStub
  LawDetailScraper : fetches individual law pages, extracts metadata + full text
  PDFDownloader    : downloads PDF attachments for Bronze-layer archiving

Sources supported
─────────────────
  Primary  : https://www.lawerp.com  (HTML, Arabic)
  Fallback : Playwright for JS-rendered pages (auto-detected)

Usage
─────
  python scraper.py --years 2020-2024 --output ./output/laws.jsonl
"""

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config import ScraperConfig, LogConfig

# ─── Logging Setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LogConfig.LEVEL),
    format=LogConfig.FORMAT,
    datefmt=LogConfig.DATE_FORMAT,
)
logger = logging.getLogger(__name__)


# ─── Data Models ────────────────────────────────────────────────────────────────
@dataclass
class LawStub:
    """Minimal record scraped from the index/listing page."""
    law_number: str
    year: int
    title: str
    category: str
    detail_url: str


@dataclass
class Article:
    """Single article parsed from a law's full text."""
    article_number: str       # e.g. "1", "2", "الأولى", "الثانية"
    content: str


@dataclass
class Law:
    """Complete law record ready for insertion into the Silver layer."""
    law_number: str
    year: int
    title: str
    category: str
    status: str               # "ساري" (active) | "ملغي" (repealed)
    issue_date: Optional[str] # ISO-8601 date string
    source_url: str
    pdf_url: Optional[str]
    raw_text: str
    articles: list[Article] = field(default_factory=list)
    scraped_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["articles"] = [asdict(a) for a in self.articles]
        return d


# ─── HTTP Session with Retry & UA Rotation ──────────────────────────────────────
class ScraperSession:
    """
    Wraps httpx.Client with:
      • randomized User-Agent rotation
      • exponential backoff via tenacity
      • configurable inter-request delay (rate limiting)
    """

    def __init__(self) -> None:
        self._ua_pool = ScraperConfig.USER_AGENTS.copy()
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(
                connect=ScraperConfig.CONNECT_TIMEOUT,
                read=ScraperConfig.READ_TIMEOUT,
            ),
            headers=self._build_headers(),
            http2=True,
        )

    def _build_headers(self) -> dict:
        return {
            "User-Agent": random.choice(self._ua_pool),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
        }

    def _rotate_ua(self) -> None:
        """Rotate User-Agent on each request to reduce fingerprinting."""
        self.client.headers["User-Agent"] = random.choice(self._ua_pool)

    def _sleep(self) -> None:
        """Respect rate limit with randomized delay."""
        delay = random.uniform(
            ScraperConfig.REQUEST_DELAY_MIN,
            ScraperConfig.REQUEST_DELAY_MAX,
        )
        logger.debug("Rate-limit sleep: %.2f s", delay)
        time.sleep(delay)

    @retry(
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)
        ),
        stop=stop_after_attempt(ScraperConfig.MAX_RETRIES),
        wait=wait_exponential(
            multiplier=ScraperConfig.BACKOFF_BASE,
            max=ScraperConfig.BACKOFF_MAX,
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def get(self, url: str, **kwargs) -> httpx.Response:
        """GET with automatic retry, back-off, and UA rotation."""
        self._rotate_ua()
        self._sleep()
        logger.info("GET %s", url)
        response = self.client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ─── Playwright Fallback (JS-Rendered Pages) ────────────────────────────────────
def _playwright_get_html(url: str) -> str:
    """
    Fetch a JS-heavy page using Playwright (headless Chromium).
    Only imported when needed to avoid hard dependency.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && "
            "playwright install chromium"
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=random.choice(ScraperConfig.USER_AGENTS)
        )
        page.goto(url, wait_until="networkidle", timeout=60_000)
        html = page.content()
        browser.close()
    return html


# ─── Article Parser ─────────────────────────────────────────────────────────────
class ArticleParser:
    """
    Splits Arabic legal text into individual articles using Regex.

    Handles two common Arabic ordinal patterns:
      Pattern A: مادة (12)       — numeric ordinal
      Pattern B: المادة الأولى   — written ordinal for article 1 / 2 / 3 …
    """

    # Matches: "مادة (12)" or "مادة 12"
    _NUMERIC_RE = re.compile(
        r"(?:^|\n)\s*(مادة|المادة)\s*[\(\[（]?\s*(\d+)\s*[\)\]）]?",
        re.MULTILINE,
    )

    # Maps Arabic written ordinals → integers
    _ORDINAL_MAP: dict[str, str] = {
        "الأولى": "1", "الثانية": "2", "الثالثة": "3",
        "الرابعة": "4", "الخامسة": "5", "السادسة": "6",
        "السابعة": "7", "الثامنة": "8", "التاسعة": "9",
        "العاشرة": "10",
        "الأول": "1", "الثاني": "2", "الثالث": "3",
        "الرابع": "4", "الخامس": "5", "السادس": "6",
        "السابع": "7", "الثامن": "8", "التاسع": "9",
        "العاشر": "10",
    }

    # Matches: "المادة الأولى" / "مادة الثانية"
    _ORDINAL_RE = re.compile(
        r"(?:^|\n)\s*(?:المادة|مادة)\s+(" + "|".join(_ORDINAL_MAP.keys()) + r")",
        re.MULTILINE,
    )

    @classmethod
    def parse(cls, raw_text: str) -> list[Article]:
        """Return a list of Article objects extracted from raw_text."""
        articles: list[Article] = []

        # Normalise line endings
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()

        # Collect all split points (position, article_number)
        split_points: list[tuple[int, str]] = []

        for m in cls._NUMERIC_RE.finditer(text):
            split_points.append((m.start(), m.group(2)))

        for m in cls._ORDINAL_RE.finditer(text):
            ordinal_word = m.group(1)
            num = cls._ORDINAL_MAP.get(ordinal_word, ordinal_word)
            split_points.append((m.start(), num))

        # Remove duplicates and sort by position
        split_points = sorted(set(split_points), key=lambda x: x[0])

        if not split_points:
            logger.warning("No article boundaries found; returning raw text as single article.")
            return [Article(article_number="1", content=text)]

        # Slice text between successive split points
        for idx, (pos, art_num) in enumerate(split_points):
            end_pos = split_points[idx + 1][0] if idx + 1 < len(split_points) else len(text)
            content = text[pos:end_pos].strip()
            if content:
                articles.append(Article(article_number=art_num, content=content))

        return articles


# ─── Law Index Scraper ──────────────────────────────────────────────────────────
class LawIndexScraper:
    """
    Paginates through lawerp.com's law listing pages and yields LawStub objects.
    Extend `_parse_index_page` to handle the site's actual HTML structure.
    """

    def __init__(self, session: ScraperSession, years: Optional[list[int]] = None) -> None:
        self.session = session
        self.years = years  # filter to specific years; None = all years

    def _build_list_url(self, page: int = 1, year: Optional[int] = None) -> str:
        """Construct the URL for a paginated law index."""
        url = f"{ScraperConfig.LAWS_LIST_URL}?page={page}"
        if year:
            url += f"&year={year}"
        return url

    def _parse_index_page(self, html: str, base_url: str) -> list[LawStub]:
        """
        Parse a single index page and return a list of LawStub objects.

        NOTE: Adapt the CSS selectors below to match the actual site's HTML.
        The selectors shown are representative; inspect the live site and update.
        """
        soup = BeautifulSoup(html, "lxml")
        stubs: list[LawStub] = []

        # --- Adapt selectors to the live site ---
        for row in soup.select("table.laws-table tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            try:
                link_tag = cells[1].find("a")
                if not link_tag:
                    continue
                stubs.append(
                    LawStub(
                        law_number=cells[0].get_text(strip=True),
                        year=int(cells[2].get_text(strip=True) or 0),
                        title=link_tag.get_text(strip=True),
                        category=cells[3].get_text(strip=True),
                        detail_url=urljoin(base_url, link_tag["href"]),
                    )
                )
            except (ValueError, IndexError, KeyError) as exc:
                logger.warning("Skipping malformed row: %s", exc)

        return stubs

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """Return True if a 'next page' link/button exists."""
        next_btn = soup.select_one("a.pagination-next, li.next a, a[rel='next']")
        return next_btn is not None

    def iter_stubs(self) -> Generator[LawStub, None, None]:
        """Yield LawStub objects for all laws matching the configured years."""
        year_list = self.years or [None]  # type: ignore[list-item]

        for year in year_list:
            page = 1
            while True:
                url = self._build_list_url(page=page, year=year)
                try:
                    resp = self.session.get(url)
                    html = resp.text
                except Exception as exc:
                    logger.error("Failed to fetch index page %s: %s", url, exc)
                    break

                soup = BeautifulSoup(html, "lxml")
                stubs = self._parse_index_page(html, url)
                logger.info("Page %d | year=%s | found %d stubs", page, year, len(stubs))

                for stub in stubs:
                    yield stub

                if not self._has_next_page(soup):
                    break
                page += 1


# ─── Law Detail Scraper ─────────────────────────────────────────────────────────
class LawDetailScraper:
    """
    Fetches a single law's detail page and extracts metadata + full text.
    Falls back to Playwright if the page requires JavaScript rendering.
    """

    def __init__(self, session: ScraperSession) -> None:
        self.session = session
        self.parser = ArticleParser()

    def _extract_metadata(self, soup: BeautifulSoup, stub: LawStub, url: str) -> dict:
        """
        Extract metadata fields from a law detail page.
        Adapt selectors to the live site structure.
        """
        # --- Issue date ---
        date_tag = soup.select_one(".law-issue-date, span[data-field='issue_date']")
        issue_date: Optional[str] = None
        if date_tag:
            raw_date = date_tag.get_text(strip=True)
            # Parse Arabic date: "15 مارس 2021" → "2021-03-15"
            issue_date = _parse_arabic_date(raw_date)

        # --- Status ---
        status_tag = soup.select_one(".law-status, span[data-field='status']")
        status = "ساري"
        if status_tag:
            raw_status = status_tag.get_text(strip=True)
            status = "ملغي" if "ملغي" in raw_status or "مُلغى" in raw_status else "ساري"

        # --- PDF link ---
        pdf_tag = soup.select_one("a[href$='.pdf'], a.download-pdf")
        pdf_url: Optional[str] = None
        if pdf_tag:
            pdf_url = urljoin(url, pdf_tag["href"])

        # --- Full text ---
        text_container = soup.select_one(".law-content, article.law-body, div#law-text")
        raw_text = text_container.get_text(separator="\n", strip=True) if text_container else ""

        return {
            "issue_date": issue_date,
            "status": status,
            "pdf_url": pdf_url,
            "raw_text": raw_text,
        }

    def scrape(self, stub: LawStub) -> Optional[Law]:
        """Scrape full details for a LawStub; returns None on persistent failure."""
        url = stub.detail_url
        try:
            resp = self.session.get(url)
            html = resp.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                logger.warning("403 on %s — retrying with Playwright", url)
                try:
                    html = _playwright_get_html(url)
                except Exception as pw_exc:
                    logger.error("Playwright also failed for %s: %s", url, pw_exc)
                    return None
            else:
                logger.error("HTTP %s on %s", exc.response.status_code, url)
                return None
        except Exception as exc:
            logger.error("Failed to fetch detail page %s: %s", url, exc)
            return None

        soup = BeautifulSoup(html, "lxml")
        meta = self._extract_metadata(soup, stub, url)

        articles = ArticleParser.parse(meta["raw_text"]) if meta["raw_text"] else []

        law = Law(
            law_number=stub.law_number,
            year=stub.year,
            title=stub.title,
            category=stub.category,
            status=meta["status"],
            issue_date=meta["issue_date"],
            source_url=url,
            pdf_url=meta["pdf_url"],
            raw_text=meta["raw_text"],
            articles=articles,
        )
        logger.info(
            "Scraped law %s/%s — %d articles", stub.law_number, stub.year, len(articles)
        )
        return law


# ─── PDF Downloader ─────────────────────────────────────────────────────────────
class PDFDownloader:
    """Downloads PDF attachments to a local cache directory for Bronze-layer upload."""

    def __init__(self, session: ScraperSession, cache_dir: Optional[Path] = None) -> None:
        self.session = session
        self.cache_dir = cache_dir or ScraperConfig.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def download(self, pdf_url: str, law_number: str, year: int) -> Optional[Path]:
        """
        Download a PDF to cache_dir/<year>/<law_number>.pdf.
        Returns the local path or None on failure.
        """
        safe_name = re.sub(r"[^\w\-.]", "_", f"{year}_{law_number}.pdf")
        dest = self.cache_dir / str(year) / safe_name
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            logger.debug("PDF already cached: %s", dest)
            return dest

        try:
            resp = self.session.get(pdf_url)
            dest.write_bytes(resp.content)
            logger.info("Downloaded PDF → %s (%d bytes)", dest, len(resp.content))
            return dest
        except Exception as exc:
            logger.error("PDF download failed for %s: %s", pdf_url, exc)
            return None


# ─── Helpers ─────────────────────────────────────────────────────────────────────
_ARABIC_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4,
    "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8,
    "سبتمبر": 9, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    # Alternative spellings
    "ابريل": 4, "ماي": 5, "يوليه": 7, "اغسطس": 8,
    "سبتمبرا": 9, "اكتوبر": 10,
}


def _parse_arabic_date(raw: str) -> Optional[str]:
    """
    Parse a human-readable Arabic date string into ISO-8601 (YYYY-MM-DD).
    Examples: "15 مارس 2021" → "2021-03-15"
              "2021/03/15"   → "2021-03-15"
    """
    raw = raw.strip()
    # Try ISO / numeric formats first
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass

    # Try Arabic month name
    pattern = re.compile(r"(\d{1,2})\s+([^\d]+?)\s+(\d{4})")
    m = pattern.search(raw)
    if m:
        day, month_word, year = int(m.group(1)), m.group(2).strip(), int(m.group(3))
        month = _ARABIC_MONTHS.get(month_word)
        if month:
            try:
                return datetime(year, month, day).date().isoformat()
            except ValueError:
                pass

    logger.warning("Could not parse date: %r", raw)
    return None


# ─── Main Orchestration ──────────────────────────────────────────────────────────
def run_scraper(
    years: Optional[list[int]] = None,
    output_path: Path = Path("output/laws.jsonl"),
    skip_existing: Optional[set[tuple[str, int]]] = None,
) -> list[Law]:
    """
    Entry point for the scraping pipeline.

    Args:
        years          : list of years to scrape; None = all available.
        output_path    : JSONL file to append scraped laws to.
        skip_existing  : set of (law_number, year) tuples already in DB.

    Returns:
        List of newly scraped Law objects.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    skip_existing = skip_existing or set()
    scraped: list[Law] = []

    with ScraperSession() as session:
        index_scraper = LawIndexScraper(session, years=years)
        detail_scraper = LawDetailScraper(session)
        pdf_downloader = PDFDownloader(session)

        for stub in index_scraper.iter_stubs():
            key = (stub.law_number, stub.year)
            if key in skip_existing:
                logger.info("Skipping already-ingested law %s/%s", *key)
                continue

            law = detail_scraper.scrape(stub)
            if law is None:
                continue

            # Download PDF to local cache (will be uploaded to GCS separately)
            if law.pdf_url:
                pdf_downloader.download(law.pdf_url, law.law_number, law.year)

            # Append to JSONL output
            with output_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(law.to_dict(), ensure_ascii=False) + "\n")

            scraped.append(law)

    logger.info("Scraping complete. Total new laws scraped: %d", len(scraped))
    return scraped


# ─── CLI Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Egyptian Laws Scraper")
    ap.add_argument(
        "--years",
        type=str,
        default=None,
        help="Comma-separated years, e.g. 2020,2021,2022. Omit for all years.",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("output/laws.jsonl"),
        help="Output JSONL file path.",
    )
    args = ap.parse_args()

    year_list = (
        [int(y.strip()) for y in args.years.split(",")]
        if args.years
        else None
    )
    run_scraper(years=year_list, output_path=args.output)
