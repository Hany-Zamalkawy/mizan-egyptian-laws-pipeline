"""
db_loader.py — Silver/Gold Layer: Load scraped Laws into PostgreSQL.

Responsibilities
────────────────
  DBLoader.get_existing_keys()   : query DB for (law_number, year) to skip
  DBLoader.upsert_law()          : call upsert_law stored procedure
  DBLoader.upsert_articles()     : call upsert_article for each article
  DBLoader.load_laws()           : batch-load a list of Law dicts

Incremental Logic
─────────────────
  Before scraping, the pipeline fetches all (law_number, year) already in
  PostgreSQL.  The scraper skips those keys.  Any newly scraped laws are
  inserted via ON CONFLICT DO UPDATE (idempotent upsert).
"""

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Generator, Optional

import psycopg2
import psycopg2.extras

from config import DBConfig, LogConfig

# ─── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LogConfig.LEVEL),
    format=LogConfig.FORMAT,
    datefmt=LogConfig.DATE_FORMAT,
)
logger = logging.getLogger(__name__)

# Register UUID adapter
psycopg2.extras.register_uuid()


# ─── Database Loader ─────────────────────────────────────────────────────────────
class DBLoader:
    """
    Manages all PostgreSQL interactions for the Egypt Legal Data Pipeline.

    Parameters
    ----------
    dsn : str | None
        libpq DSN string.  Defaults to DBConfig.dsn().
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or DBConfig.dsn()
        self._conn: Optional[psycopg2.extensions.connection] = None

    # ── Connection Management ─────────────────────────────────────────────────
    def connect(self) -> None:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = False
            logger.info("Connected to PostgreSQL at %s:%s/%s",
                        DBConfig.HOST, DBConfig.PORT, DBConfig.NAME)

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.info("PostgreSQL connection closed.")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            if self._conn:
                self._conn.rollback()
        else:
            if self._conn:
                self._conn.commit()
        self.close()

    @contextmanager
    def cursor(self) -> Generator:
        self.connect()
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # ── Schema Initialisation ─────────────────────────────────────────────────
    def init_schema(self, schema_path: str = "schema.sql") -> None:
        """Execute schema.sql to create tables, indexes, and functions."""
        with open(schema_path, "r", encoding="utf-8") as fh:
            sql = fh.read()
        with self.cursor() as cur:
            cur.execute(sql)
        logger.info("Database schema initialised from %s.", schema_path)

    # ── Incremental Deduplication ─────────────────────────────────────────────
    def get_existing_keys(self) -> set[tuple[str, int]]:
        """Return all (law_number, year) pairs already stored in laws table."""
        with self.cursor() as cur:
            cur.execute("SELECT law_number, year FROM laws;")
            keys = {(row["law_number"], int(row["year"])) for row in cur.fetchall()}
        logger.info("Found %d existing laws in PostgreSQL.", len(keys))
        return keys

    # ── Upsert Operations ─────────────────────────────────────────────────────
    def upsert_law(
        self,
        law_number: str,
        year: int,
        title: str,
        category: str,
        status: str,
        issue_date: Optional[str],     # ISO date string or None
        source_url: Optional[str],
        pdf_gcs_uri: Optional[str],
        json_gcs_uri: Optional[str],
        raw_text: Optional[str],
    ) -> uuid.UUID:
        """
        Insert or update a law record.  Returns the law_id UUID.
        Uses the upsert_law stored procedure defined in schema.sql.
        """
        # Map unknown categories / statuses to safe defaults
        valid_categories = {"قانون", "مرسوم", "قرار", "لائحة", "دستور", "اتفاقية", "أخرى"}
        valid_statuses   = {"ساري", "ملغي", "معدل", "موقوف"}

        safe_category = category if category in valid_categories else "أخرى"
        safe_status   = status   if status   in valid_statuses   else "ساري"

        parsed_date: Optional[date] = None
        if issue_date:
            try:
                from datetime import datetime as dt
                parsed_date = dt.fromisoformat(issue_date).date()
            except ValueError:
                logger.warning("Invalid issue_date format: %r", issue_date)

        with self.cursor() as cur:
            cur.execute(
                "CALL upsert_law(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);",
                (
                    law_number, year, title,
                    safe_category, safe_status,
                    parsed_date, source_url,
                    pdf_gcs_uri, json_gcs_uri, raw_text,
                    None,   # OUT parameter placeholder
                ),
            )
            # The stored procedure returns law_id via the OUT parameter
            # For psycopg2, fetch it from the result set
            # Alternative: use RETURNING in the procedure
            cur.execute(
                "SELECT law_id FROM laws WHERE law_number=%s AND year=%s;",
                (law_number, year),
            )
            row = cur.fetchone()
            law_id = row["law_id"]

        logger.debug("Upserted law %s/%s → law_id=%s", law_number, year, law_id)
        return law_id

    def upsert_articles(self, law_id: uuid.UUID, articles: list[dict]) -> int:
        """
        Upsert all articles for a given law.

        Parameters
        ----------
        law_id   : UUID of the parent law record.
        articles : list of dicts with keys 'article_number' and 'content'.

        Returns
        -------
        Number of articles upserted.
        """
        if not articles:
            return 0

        sql = """
            INSERT INTO articles (law_id, article_number, content)
            VALUES (%s, %s, %s)
            ON CONFLICT (law_id, article_number)
            DO UPDATE SET content = EXCLUDED.content;
        """
        rows = [
            (str(law_id), art["article_number"], art["content"])
            for art in articles
        ]

        with self.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=100)

        logger.debug("Upserted %d articles for law_id=%s", len(rows), law_id)
        return len(rows)

    # ── Batch Load ────────────────────────────────────────────────────────────
    def load_laws(
        self,
        laws: list[dict],
        pdf_uris: Optional[dict[str, str]] = None,
        json_uris: Optional[dict[str, str]] = None,
    ) -> dict:
        """
        Batch-load a list of law dicts into PostgreSQL.

        Parameters
        ----------
        laws      : list of law dicts (as returned by Law.to_dict()).
        pdf_uris  : optional mapping of "law_number/year" → GCS PDF URI.
        json_uris : optional mapping of "law_number/year" → GCS JSON URI.

        Returns
        -------
        Summary dict: {inserted, updated, articles_total, failed}.
        """
        pdf_uris  = pdf_uris  or {}
        json_uris = json_uris or {}

        stats = {"inserted": 0, "articles_total": 0, "failed": 0}

        for law in laws:
            law_number = law["law_number"]
            year       = int(law["year"])
            key        = f"{law_number}/{year}"

            try:
                law_id = self.upsert_law(
                    law_number   = law_number,
                    year         = year,
                    title        = law.get("title", ""),
                    category     = law.get("category", "قانون"),
                    status       = law.get("status", "ساري"),
                    issue_date   = law.get("issue_date"),
                    source_url   = law.get("source_url"),
                    pdf_gcs_uri  = pdf_uris.get(key),
                    json_gcs_uri = json_uris.get(key),
                    raw_text     = law.get("raw_text"),
                )
                art_count = self.upsert_articles(law_id, law.get("articles", []))
                stats["inserted"]       += 1
                stats["articles_total"] += art_count
            except Exception as exc:
                logger.error("Failed to load law %s: %s", key, exc)
                stats["failed"] += 1

        logger.info(
            "DB load complete: %d laws, %d articles, %d failed",
            stats["inserted"], stats["articles_total"], stats["failed"],
        )
        return stats

    # ── Scrape Log ────────────────────────────────────────────────────────────
    def log_run(
        self,
        dag_run_id: str,
        laws_checked: int,
        laws_new: int,
        laws_failed: int,
        duration_seconds: float,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Insert a row into the scrape_log table."""
        sql = """
            INSERT INTO scrape_log
                (dag_run_id, laws_checked, laws_new, laws_failed,
                 duration_seconds, status, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """
        with self.cursor() as cur:
            cur.execute(sql, (
                dag_run_id, laws_checked, laws_new,
                laws_failed, duration_seconds, status, error_message,
            ))
        logger.info("Scrape run logged: %s → %s", dag_run_id, status)
