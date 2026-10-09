"""
config.py — Centralized configuration for the Egypt Legal Data Pipeline.

All environment-specific values are read from environment variables or a
.env file so that no secrets are hard-coded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ─── Load .env file (if present) ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# ─── GCP Settings ──────────────────────────────────────────────────────────────
class GCPConfig:
    # Path to your downloaded Service Account JSON key file.
    # Set via env var GCP_KEY_PATH or place 'gcp_key.json' next to this file.
    SERVICE_ACCOUNT_KEY: str = os.getenv(
        "GCP_KEY_PATH", str(BASE_DIR / "gcp_key.json")
    )
    PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "your-gcp-project-id")
    RAW_BUCKET: str = os.getenv("GCS_RAW_BUCKET", "egypt-laws-raw-data")
    # Folder prefixes inside the bucket
    RAW_JSON_PREFIX: str = "raw/json"
    RAW_PDF_PREFIX: str = "raw/pdf"


# ─── PostgreSQL Settings ────────────────────────────────────────────────────────
class DBConfig:
    HOST: str = os.getenv("PG_HOST", "localhost")
    PORT: int = int(os.getenv("PG_PORT", "5432"))
    NAME: str = os.getenv("PG_DB", "egypt_laws")
    USER: str = os.getenv("PG_USER", "airflow")
    PASSWORD: str = os.getenv("PG_PASSWORD", "changeme")

    @classmethod
    def dsn(cls) -> str:
        """Return a libpq-style DSN connection string."""
        return (
            f"postgresql://{cls.USER}:{cls.PASSWORD}"
            f"@{cls.HOST}:{cls.PORT}/{cls.NAME}"
        )


# ─── Scraper Settings ───────────────────────────────────────────────────────────
class ScraperConfig:
    # Base URL for Egypt's Official Gazette (Al-Jarida Al-Rasmiyya)
    BASE_URL: str = "https://www.lawerp.com"
    LAWS_LIST_URL: str = f"{BASE_URL}/laws"

    # HTTP client timeouts (seconds)
    CONNECT_TIMEOUT: float = 15.0
    READ_TIMEOUT: float = 60.0

    # Retry / back-off
    MAX_RETRIES: int = 5
    BACKOFF_BASE: float = 2.0          # seconds; actual delay = base ** attempt
    BACKOFF_MAX: float = 120.0         # cap (seconds)
    JITTER_RANGE: float = 3.0         # ±seconds of random jitter

    # Rate-limiting: wait this many seconds between consecutive requests
    REQUEST_DELAY_MIN: float = 1.5
    REQUEST_DELAY_MAX: float = 4.5

    # A pool of User-Agent strings to rotate through
    USER_AGENTS: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
    ]

    # Local cache directory for downloaded PDFs before GCS upload
    CACHE_DIR: Path = BASE_DIR / "cache"


# ─── Airflow DAG Settings ───────────────────────────────────────────────────────
class AirflowConfig:
    DAG_ID: str = "egypt_laws_pipeline"
    SCHEDULE: str = "0 2 * * *"        # 02:00 AM UTC daily
    START_DATE_YEAR: int = 2024
    START_DATE_MONTH: int = 1
    START_DATE_DAY: int = 1
    RETRIES: int = 3
    RETRY_DELAY_MINUTES: int = 5
    EMAIL_ON_FAILURE: bool = True
    FAILURE_EMAIL: str = os.getenv("ALERT_EMAIL", "devops@example.com")


# ─── Logging ────────────────────────────────────────────────────────────────────
class LogConfig:
    LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    FORMAT: str = (
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
    )
    DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
