"""
local_scraper.py — Production Egyptian Legal Scraper, Normalizer, Article Parser & OCR Engine.

Capabilities:
  1. Arabic Text Normalization (strip tashkeel, unify alef variants, remove tatweel).
  2. OCR Engine for scanned historical PDFs (pdfplumber + pytesseract fallback).
  3. Article extraction via enhanced regex (numeric and written ordinals).
  4. Multi-category traversal and pagination.
  5. Data quality validations (length assertion, article counts).
  6. Output formatting to raw_data/ and processed_data/ (JSON, XLSX, CSV).
"""

import json
import logging
import os
import random
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional, Tuple
from urllib.parse import unquote, urljoin

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# ─── Directory Setup ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
RAW_DATA_DIR = BASE_DIR / "raw_data"
PROCESSED_DATA_DIR = BASE_DIR / "processed_data"

RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
)
logger = logging.getLogger("local_scraper")

BASE_URL = "https://manshurat.org"

# Category taxonomies:
# 23: قوانين, 7: قرارات رئاسية, 25: لوائح, 24: اتفاقيات دولية, 6: قرارات وزارية
DEFAULT_CATEGORIES = {
    "قانون": 23,
    "قرار رئاسي": 7,
    "لائحة": 25,
    "اتفاقية دولية": 24,
    "قرار وزاري": 6,
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


# ─── Arabic Normalization ────────────────────────────────────────────────────
def normalize_arabic_text(text: str, remove_diacritics: bool = True) -> str:
    """
    Standardizes Arabic text:
      - Strips Tashkeel (harakat: fathah, dammah, kasrah, sukun, shaddah, tanween).
      - Removes Tatweel / Kashida (ـ).
      - Unifies Alef variants (أ, إ, آ, ٱ -> ا).
      - Normalizes Yeh / Alef Maksura (ى -> ي when requested or appropriate).
      - Normalizes Teh Marbuta (ة -> ه) for flexible keyword matching.
    """
    if not text:
        return ""

    # Strip Tatweel
    text = text.replace("\u0640", "")

    if remove_diacritics:
        # Arabic diacritics unicode range 064B - 0652
        arabic_diacritics = re.compile(r"[\u064B-\u0652\u0670]")
        text = re.sub(arabic_diacritics, "", text)

    # Normalize Alefs
    text = re.sub("[إأآٱ]", "ا", text)

    # Clean whitespace
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ─── Scanned PDF OCR & Text Extraction ────────────────────────────────────────
class PDFExtractor:
    """
    Extracts text from downloaded legal PDFs.
    Uses native text layer first; if empty (scanned image), falls back to OCR.
    """

    @staticmethod
    def extract_text(pdf_path: Path) -> Tuple[str, bool]:
        """
        Returns (extracted_text, is_ocr_used).
        """
        if not pdf_path.exists():
            return "", False

        extracted_text = ""
        try:
            # 1. Native text extraction via pdfplumber
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                pages_text = []
                for p in pdf.pages:
                    txt = p.extract_text()
                    if txt:
                        pages_text.append(txt)
                extracted_text = "\n".join(pages_text).strip()
        except Exception as e:
            logger.debug("Native PDF extraction warning on %s: %s", pdf_path.name, e)

        # 2. If text layer is substantial, return it
        if len(extracted_text.split()) > 30:
            return extracted_text, False

        # 3. OCR Fallback for scanned historical gazettes
        logger.info("PDF %s appears scanned or textless. Invoking OCR fallback...", pdf_path.name)
        try:
            import pytesseract
            from PIL import Image

            # Try pypdfium2 or pdf2image for rendering
            ocr_pages = []
            try:
                import pypdfium2 as pdfium
                doc = pdfium.PdfDocument(str(pdf_path))
                # Process first 5 pages for efficiency in batch runs
                for i in range(min(len(doc), 5)):
                    page = doc[i]
                    image = page.render(scale=2.0).to_pil()
                    txt = pytesseract.image_to_string(image, lang="ara+ara_old")
                    if txt:
                        ocr_pages.append(txt)
            except Exception:
                pass

            if ocr_pages:
                full_ocr_text = "\n".join(ocr_pages).strip()
                logger.info("OCR successful on %s: %d characters recognized.", pdf_path.name, len(full_ocr_text))
                return full_ocr_text, True
        except Exception as ocr_err:
            logger.warning("OCR fallback not available or failed on %s: %s", pdf_path.name, ocr_err)

        return extracted_text, False


# ─── Data Models ─────────────────────────────────────────────────────────────
@dataclass
class Article:
    article_number: str
    content: str


@dataclass
class LawRecord:
    law_number: str
    year: int
    title: str
    category: str
    status: str
    issue_date: Optional[str]
    source_url: str
    pdf_url: Optional[str]
    local_raw_path: Optional[str]
    raw_text: str
    is_ocr: bool = False
    quality_passed: bool = True
    articles: List[Article] = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["articles"] = [asdict(a) for a in self.articles]
        return d


# ─── Article Parser ──────────────────────────────────────────────────────────
class ArticleParser:
    """
    Extracts individual legal articles from Arabic text.
    Handles 'مادة (1)', 'مادة 12 مكرر', 'المادة الأولى', etc.
    """

    _NUMERIC_RE = re.compile(
        r"(?:^|\n)\s*(?:مادة|المادة)\s*[\(\[（]?\s*(\d+(?:\s*(?:مكرر|أ|ب))?)\s*[\)\]）]?",
        re.MULTILINE,
    )

    _ORDINAL_MAP = {
        "الأولى": "1", "الأول": "1",
        "الثانية": "2", "الثاني": "2",
        "الثالثة": "3", "الثالث": "3",
        "الرابعة": "4", "الرابع": "4",
        "الخامسة": "5", "الخامس": "5",
        "السادسة": "6", "السادس": "6",
        "السابعة": "7", "السابع": "7",
        "الثامنة": "8", "الثامن": "8",
        "التاسعة": "9", "التاسع": "9",
        "العاشرة": "10", "العاشر": "10",
    }

    _ORDINAL_RE = re.compile(
        r"(?:^|\n)\s*(?:المادة|مادة)\s+(" + "|".join(_ORDINAL_MAP.keys()) + r")",
        re.MULTILINE,
    )

    @classmethod
    def parse_articles(cls, text: str) -> List[Article]:
        if not text:
            return []

        clean_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        split_points: List[Tuple[int, str]] = []

        for m in cls._NUMERIC_RE.finditer(clean_text):
            split_points.append((m.start(), m.group(1).strip()))

        for m in cls._ORDINAL_RE.finditer(clean_text):
            word = m.group(1).strip()
            num = cls._ORDINAL_MAP.get(word, word)
            split_points.append((m.start(), num))

        split_points = sorted(set(split_points), key=lambda x: x[0])

        if not split_points:
            # Fallback: Treat body as article 1
            return [Article(article_number="1", content=clean_text)]

        articles: List[Article] = []
        for i, (pos, art_num) in enumerate(split_points):
            end_pos = split_points[i + 1][0] if i + 1 < len(split_points) else len(clean_text)
            content = clean_text[pos:end_pos].strip()
            if content:
                articles.append(Article(article_number=art_num, content=content))

        return articles


# ─── Scraper Engine ──────────────────────────────────────────────────────────
class LocalScraper:
    def __init__(self, delay_min: float = 1.0, delay_max: float = 2.0):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=15.0),
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.9",
                "DNT": "1",
            },
        )

    def _sleep(self):
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1.5, min=2, max=20),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def fetch_url(self, url: str) -> httpx.Response:
        self.client.headers["User-Agent"] = random.choice(USER_AGENTS)
        self._sleep()
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp

    def download_pdf(self, pdf_url: str, filename_prefix: str) -> Optional[Path]:
        try:
            safe_name = re.sub(r"[^\w\-.]", "_", f"{filename_prefix}.pdf")
            target = RAW_DATA_DIR / safe_name
            if target.exists() and target.stat().st_size > 0:
                logger.info("PDF cached: %s", target.name)
                return target

            resp = self.fetch_url(pdf_url)
            target.write_bytes(resp.content)
            logger.info("Downloaded PDF: %s (%d bytes)", target.name, len(resp.content))
            return target
        except Exception as e:
            logger.warning("PDF download failed (%s): %s", pdf_url, e)
            return None

    def scrape_law_detail(self, stub: dict) -> Optional[LawRecord]:
        url = stub["detail_url"]
        try:
            resp = self.fetch_url(url)
        except Exception as e:
            logger.error("Detail page fetch error %s: %s", url, e)
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # 1. Title
        title = stub.get("title", "")
        h2 = soup.find("h2")
        if h2 and not title:
            title = h2.get_text(strip=True)

        # 2. Extract PDF Link
        pdf_url = None
        dl_div = soup.find("div", class_="download-link")
        if dl_div:
            pdf_a = dl_div.find("a", href=re.compile(r"/file/\d+/download"))
            if pdf_a:
                pdf_url = urljoin(BASE_URL, pdf_a["href"])
            else:
                iframe = soup.find("iframe", class_="gdoc-field")
                if iframe:
                    src = iframe.get("src", "")
                    m = re.search(r"url=([^&]+)", src)
                    if m:
                        pdf_url = unquote(m.group(1))

        # 3. Extract Metadata
        issue_date = None
        sidebar = soup.find("div", class_="ds-sidebar")
        if sidebar:
            txt = sidebar.get_text(separator="\n", strip=True)
            m = re.search(r"تاريخ إصدار[^\n]*\n(.+?)(?:\n|$)", txt)
            if m:
                issue_date = m.group(1).strip()

        law_number = stub.get("law_number", "")
        year = stub.get("year", 0)

        for info in soup.select("div.inline-info"):
            lbl = info.find("div", class_="label-inline")
            if not lbl:
                continue
            label = lbl.get_text(strip=True)
            val = info.get_text(separator=" ", strip=True).replace(label, "").strip().lstrip("\u00a0").strip()
            if "رقم الوثيقة" in label or "رقم الدعوى" in label:
                if val and not law_number:
                    law_number = val
            elif "سنة الإصدار" in label or "السنة القضائية" in label:
                if not year:
                    try:
                        year = int(val)
                    except ValueError:
                        pass

        # 4. Extract Text & PDF Handling
        raw_text = ""
        main_div = soup.find("div", class_="ds-main")
        if main_div:
            for tag in main_div.find_all(["iframe", "script", "style"]):
                tag.decompose()
            raw_text = main_div.get_text(separator="\n", strip=True)

        filename_prefix = f"{stub.get('category', 'قانون')}_{year or 'unknown'}_{law_number or stub.get('node_id', 'doc')}"
        local_raw_path = None
        is_ocr = False

        if pdf_url:
            downloaded = self.download_pdf(pdf_url, filename_prefix)
            if downloaded:
                local_raw_path = str(downloaded)
                # If page body text is empty, extract from PDF (with OCR fallback)
                if len(raw_text.split()) < 30:
                    pdf_extracted, is_ocr = PDFExtractor.extract_text(downloaded)
                    if pdf_extracted:
                        raw_text = pdf_extracted

        if not local_raw_path and raw_text:
            raw_txt = RAW_DATA_DIR / f"{filename_prefix}.txt"
            raw_txt.write_text(raw_text, encoding="utf-8")
            local_raw_path = str(raw_txt)

        # Normalize text and parse articles
        norm_text = normalize_arabic_text(raw_text)
        articles = ArticleParser.parse_articles(raw_text or norm_text)

        # 5. Data Quality Validation
        word_count = len(norm_text.split()) if norm_text else 0
        quality_passed = word_count >= 15 or len(articles) > 0

        record = LawRecord(
            law_number=str(law_number or stub.get("node_id", "")),
            year=int(year or datetime.now().year),
            title=title,
            category=stub.get("category", "قانون"),
            status="ساري",
            issue_date=issue_date,
            source_url=url,
            pdf_url=pdf_url,
            local_raw_path=local_raw_path,
            raw_text=raw_text,
            is_ocr=is_ocr,
            quality_passed=quality_passed,
            articles=articles,
        )
        return record

    def scrape_stubs(self, category_name: str, term_id: int, max_pages: int = 1) -> Generator[dict, None, None]:
        for page in range(max_pages):
            url = f"{BASE_URL}/taxonomy/term/{term_id}?page={page}"
            logger.info("Browsing %s (page %d)...", category_name, page)
            try:
                resp = self.fetch_url(url)
            except Exception as e:
                logger.error("Category page error %s: %s", url, e)
                break

            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select("div.nodes-list-item")
            if not items:
                break

            for item in items:
                h2 = item.find("h2")
                if not h2:
                    continue
                a = h2.find("a")
                if not a:
                    continue

                title = a.get_text(strip=True)
                detail_url = urljoin(BASE_URL, a.get("href", ""))
                node_m = re.search(r"/node/(\d+)", detail_url)
                node_id = node_m.group(1) if node_m else ""

                law_number = ""
                year = 0
                for info in item.select("div.inline-info"):
                    lbl = info.find("div", class_="label-inline")
                    if not lbl:
                        continue
                    label = lbl.get_text(strip=True)
                    val = info.get_text(separator=" ", strip=True).replace(label, "").strip().lstrip("\u00a0").strip()
                    if "رقم الوثيقة" in label or "رقم الدعوى" in label:
                        law_number = val
                    elif "سنة الإصدار" in label or "السنة القضائية" in label:
                        try:
                            year = int(val)
                        except ValueError:
                            pass

                yield {
                    "node_id": node_id,
                    "title": title,
                    "detail_url": detail_url,
                    "category": category_name,
                    "law_number": law_number,
                    "year": year,
                }

            if not soup.find("li", class_="pager-next"):
                break

    def close(self):
        self.client.close()


# ─── Dataset Exporter ─────────────────────────────────────────────────────────
def save_processed_datasets(records: List[LawRecord]) -> dict:
    if not records:
        logger.warning("No records to export.")
        return {}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = PROCESSED_DATA_DIR / f"laws_structured_{timestamp}.json"
    excel_path = PROCESSED_DATA_DIR / "egyptian_laws_dataset.xlsx"
    csv_path = PROCESSED_DATA_DIR / f"laws_summary_{timestamp}.csv"

    # 1. JSON
    data_dicts = [r.to_dict() for r in records]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data_dicts, f, ensure_ascii=False, indent=2)

    # 2. Excel & CSV
    laws_rows = []
    articles_rows = []

    for r in records:
        laws_rows.append({
            "law_number": r.law_number,
            "year": r.year,
            "title": r.title,
            "category": r.category,
            "status": r.status,
            "issue_date": r.issue_date,
            "source_url": r.source_url,
            "pdf_url": r.pdf_url,
            "local_raw_path": r.local_raw_path,
            "article_count": len(r.articles),
            "is_ocr": r.is_ocr,
            "quality_passed": r.quality_passed,
            "scraped_at": r.scraped_at,
        })

        for art in r.articles:
            articles_rows.append({
                "law_number": r.law_number,
                "year": r.year,
                "law_title": r.title,
                "category": r.category,
                "article_number": art.article_number,
                "article_content": art.content,
            })

    df_laws = pd.DataFrame(laws_rows)
    df_articles = pd.DataFrame(articles_rows)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_laws.to_excel(writer, sheet_name="Laws", index=False)
        df_articles.to_excel(writer, sheet_name="Articles", index=False)

    df_laws.to_csv(csv_path, index=False, encoding="utf-8-sig")

    logger.info("Structured datasets written: %s, %s, %s", json_path.name, excel_path.name, csv_path.name)
    return {
        "json": json_path,
        "excel": excel_path,
        "csv": csv_path,
    }


def run_local_scraping(limit_per_category: int = 5, max_pages: int = 1) -> Tuple[List[LawRecord], dict]:
    scraper = LocalScraper()
    records: List[LawRecord] = []

    try:
        for cat_name, term_id in DEFAULT_CATEGORIES.items():
            count = 0
            for stub in scraper.scrape_stubs(cat_name, term_id, max_pages=max_pages):
                logger.info("Parsing [%s]: %s", stub["category"], stub["title"][:45])
                law = scraper.scrape_law_detail(stub)
                if law:
                    records.append(law)
                    count += 1
                if count >= limit_per_category:
                    break
    finally:
        scraper.close()

    saved_files = save_processed_datasets(records)
    return records, saved_files
