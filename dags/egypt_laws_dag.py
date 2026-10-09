"""
dags/egypt_laws_dag.py — Apache Airflow DAG for the Egypt Legal Data Pipeline.

Schedule : Daily at 02:00 AM UTC
Pipeline :
    check_new_laws
        → scrape_and_extract
            → upload_raw_to_gcs
                → transform_and_load_postgres
                    → log_run_stats

Tasks overview
──────────────
  check_new_laws           : Query PostgreSQL for already-ingested (law_number, year)
                             pairs and push them as XCom so downstream tasks skip them.
  scrape_and_extract       : Run the scraper; push newly scraped Law dicts via XCom.
  upload_raw_to_gcs        : Upload raw JSON + cached PDFs to the Bronze GCS bucket.
  transform_and_load_postgres : Upsert laws & articles into the Silver PostgreSQL layer.
  log_run_stats            : Write pipeline run metrics to the scrape_log table.

DAG design principles
─────────────────────
  • All tasks are Python callables (no BashOperator / external scripts).
  • XCom is used only for small payloads (keys, counts).
    Large Law dicts are written to a shared JSONL file on the worker's
    local filesystem / mounted NFS / GCS FUSE mount.
  • Retries = 3 with 5-minute delay for transient network / DB failures.
  • Email alert on final failure via Airflow's built-in SMTP integration.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

# ─── Import pipeline modules ──────────────────────────────────────────────────
# These modules must be on PYTHONPATH on the Airflow worker.
# Place them in $AIRFLOW_HOME/dags/ or install them as a package.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import AirflowConfig, ScraperConfig
from scraper import run_scraper
from gcp_storage import upload_laws_to_gcs
from db_loader import DBLoader

logger = logging.getLogger(__name__)

# ─── Shared JSONL path (workers must have read-write access) ──────────────────
_JSONL_FILENAME = "scraped_laws_{run_date}.jsonl"
_OUTPUT_BASE = Path(os.environ.get("PIPELINE_OUTPUT_DIR", "/tmp/egypt_laws"))


def _run_output_path(run_date: str) -> Path:
    p = _OUTPUT_BASE / _JSONL_FILENAME.format(run_date=run_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# Task 1: Check New Laws
# ═══════════════════════════════════════════════════════════════════════════════
def check_new_laws(**context) -> None:
    """
    Query PostgreSQL for all (law_number, year) pairs already ingested.
    Pushes the set as a JSON-serialisable list of pairs via XCom.
    """
    run_date = context["ds"]                   # YYYY-MM-DD string
    logger.info("[check_new_laws] run_date=%s", run_date)

    with DBLoader() as db:
        existing_keys = db.get_existing_keys()

    # XCom values must be JSON-serialisable → convert set of tuples to list of lists
    keys_list = [[k[0], k[1]] for k in existing_keys]
    context["ti"].xcom_push(key="existing_keys", value=keys_list)
    context["ti"].xcom_push(key="run_date", value=run_date)
    logger.info("[check_new_laws] Found %d already-ingested laws.", len(keys_list))


# ═══════════════════════════════════════════════════════════════════════════════
# Task 2: Scrape & Extract
# ═══════════════════════════════════════════════════════════════════════════════
def scrape_and_extract(**context) -> None:
    """
    Run the web scraper for the current year (and previous year as safety buffer).
    Writes scraped laws to a shared JSONL file; pushes counts via XCom.
    """
    ti = context["ti"]
    run_date = ti.xcom_pull(task_ids="check_new_laws", key="run_date")
    existing_keys_raw = ti.xcom_pull(task_ids="check_new_laws", key="existing_keys") or []
    skip_existing = {(k[0], int(k[1])) for k in existing_keys_raw}

    current_year = datetime.strptime(run_date, "%Y-%m-%d").year
    target_years = [current_year - 1, current_year]   # scrape current & last year daily

    output_path = _run_output_path(run_date)
    logger.info("[scrape_and_extract] Target years: %s | Output: %s", target_years, output_path)

    start = time.time()
    scraped_laws = run_scraper(
        years=target_years,
        output_path=output_path,
        skip_existing=skip_existing,
    )
    duration = time.time() - start

    ti.xcom_push(key="laws_count",    value=len(scraped_laws))
    ti.xcom_push(key="duration_secs", value=round(duration, 2))
    ti.xcom_push(key="output_path",   value=str(output_path))
    logger.info(
        "[scrape_and_extract] Scraped %d new laws in %.1f s.", len(scraped_laws), duration
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Task 3: Upload Raw to GCS (Bronze Layer)
# ═══════════════════════════════════════════════════════════════════════════════
def upload_raw_to_gcs(**context) -> None:
    """
    Read the JSONL output from Task 2; upload each law's JSON + PDF to GCS.
    Pushes GCS URI mappings via XCom for Task 4.
    """
    ti = context["ti"]
    output_path = Path(ti.xcom_pull(task_ids="scrape_and_extract", key="output_path"))

    if not output_path.exists():
        logger.warning("[upload_raw_to_gcs] No JSONL file found at %s. Skipping.", output_path)
        ti.xcom_push(key="pdf_uris",  value={})
        ti.xcom_push(key="json_uris", value={})
        return

    # Load scraped laws from JSONL
    laws: list[dict] = []
    with output_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                laws.append(json.loads(line))

    logger.info("[upload_raw_to_gcs] Uploading %d laws to GCS…", len(laws))

    uris = upload_laws_to_gcs(laws=laws, cache_dir=ScraperConfig.CACHE_DIR)

    # Build lookup maps: "law_number/year" → GCS URI
    json_uri_map: dict[str, str] = {}
    pdf_uri_map:  dict[str, str] = {}

    for uri in uris.get("json_uris", []):
        # Parse law_number from path: raw/json/<year>/<law_number>.json
        parts = uri.split("/")
        if len(parts) >= 2:
            year      = parts[-2]
            law_num   = parts[-1].removesuffix(".json")
            json_uri_map[f"{law_num}/{year}"] = uri

    for uri in uris.get("pdf_uris", []):
        parts = uri.split("/")
        if len(parts) >= 2:
            year      = parts[-2]
            law_num   = parts[-1].removesuffix(".pdf")
            pdf_uri_map[f"{law_num}/{year}"] = uri

    ti.xcom_push(key="json_uris", value=json_uri_map)
    ti.xcom_push(key="pdf_uris",  value=pdf_uri_map)
    ti.xcom_push(key="laws_uploaded", value=len(laws))
    logger.info(
        "[upload_raw_to_gcs] Uploaded %d JSON, %d PDF to GCS.",
        len(json_uri_map), len(pdf_uri_map),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Task 4: Transform & Load PostgreSQL (Silver Layer)
# ═══════════════════════════════════════════════════════════════════════════════
def transform_and_load_postgres(**context) -> None:
    """
    Read JSONL from Task 2; upsert laws + articles into PostgreSQL Silver layer.
    Enriches records with GCS URIs from Task 3.
    """
    ti = context["ti"]
    output_path = Path(ti.xcom_pull(task_ids="scrape_and_extract", key="output_path"))
    json_uris   = ti.xcom_pull(task_ids="upload_raw_to_gcs", key="json_uris") or {}
    pdf_uris    = ti.xcom_pull(task_ids="upload_raw_to_gcs", key="pdf_uris")  or {}

    if not output_path.exists():
        logger.warning("[transform_and_load_postgres] JSONL not found. Skipping.")
        ti.xcom_push(key="db_stats", value={"inserted": 0, "articles_total": 0, "failed": 0})
        return

    laws: list[dict] = []
    with output_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                laws.append(json.loads(line))

    logger.info("[transform_and_load_postgres] Loading %d laws into PostgreSQL…", len(laws))

    with DBLoader() as db:
        stats = db.load_laws(laws=laws, pdf_uris=pdf_uris, json_uris=json_uris)

    ti.xcom_push(key="db_stats", value=stats)
    logger.info("[transform_and_load_postgres] DB stats: %s", stats)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 5: Log Run Stats
# ═══════════════════════════════════════════════════════════════════════════════
def log_run_stats(**context) -> None:
    """
    Write pipeline run metrics into the scrape_log table for observability.
    """
    ti          = context["ti"]
    dag_run_id  = context["run_id"]
    run_date    = ti.xcom_pull(task_ids="check_new_laws",         key="run_date")    or context["ds"]
    laws_count  = ti.xcom_pull(task_ids="scrape_and_extract",     key="laws_count")  or 0
    duration    = ti.xcom_pull(task_ids="scrape_and_extract",     key="duration_secs") or 0.0
    db_stats    = ti.xcom_pull(task_ids="transform_and_load_postgres", key="db_stats") or {}
    checked     = ti.xcom_pull(task_ids="check_new_laws",         key="existing_keys")
    checked_cnt = len(checked) if checked else 0

    with DBLoader() as db:
        db.log_run(
            dag_run_id       = dag_run_id,
            laws_checked     = checked_cnt + laws_count,
            laws_new         = db_stats.get("inserted", laws_count),
            laws_failed      = db_stats.get("failed", 0),
            duration_seconds = duration,
            status           = "success",
        )
    logger.info("[log_run_stats] Run %s logged successfully.", dag_run_id)


# ═══════════════════════════════════════════════════════════════════════════════
# DAG Definition
# ═══════════════════════════════════════════════════════════════════════════════
_default_args = {
    "owner":              "data-engineering",
    "depends_on_past":    False,
    "email":              [AirflowConfig.FAILURE_EMAIL],
    "email_on_failure":   AirflowConfig.EMAIL_ON_FAILURE,
    "email_on_retry":     False,
    "retries":            AirflowConfig.RETRIES,
    "retry_delay":        timedelta(minutes=AirflowConfig.RETRY_DELAY_MINUTES),
    "retry_exponential_backoff": True,
    "execution_timeout":  timedelta(hours=4),
}

with DAG(
    dag_id             = AirflowConfig.DAG_ID,
    description        = "Daily scrape, parse, and archive of Egyptian Laws & Decrees",
    default_args       = _default_args,
    start_date         = datetime(
                             AirflowConfig.START_DATE_YEAR,
                             AirflowConfig.START_DATE_MONTH,
                             AirflowConfig.START_DATE_DAY,
                         ),
    schedule_interval  = AirflowConfig.SCHEDULE,      # "0 2 * * *"
    catchup            = False,                        # don't backfill missed runs
    max_active_runs    = 1,                            # prevent overlapping runs
    tags               = ["egypt", "legal", "scraping", "gcp"],
    doc_md             = __doc__,
) as dag:

    t1_check = PythonOperator(
        task_id         = "check_new_laws",
        python_callable = check_new_laws,
        provide_context = True,
        doc_md          = "Query PostgreSQL for already-ingested law keys.",
    )

    t2_scrape = PythonOperator(
        task_id         = "scrape_and_extract",
        python_callable = scrape_and_extract,
        provide_context = True,
        doc_md          = "Run web scraper for new Egyptian Laws.",
    )

    t3_gcs = PythonOperator(
        task_id         = "upload_raw_to_gcs",
        python_callable = upload_raw_to_gcs,
        provide_context = True,
        doc_md          = "Upload raw JSON + PDFs to GCS Bronze layer.",
    )

    t4_pg = PythonOperator(
        task_id         = "transform_and_load_postgres",
        python_callable = transform_and_load_postgres,
        provide_context = True,
        doc_md          = "Upsert laws & articles into PostgreSQL Silver layer.",
    )

    t5_log = PythonOperator(
        task_id         = "log_run_stats",
        python_callable = log_run_stats,
        provide_context = True,
        doc_md          = "Write pipeline run metrics to scrape_log table.",
        trigger_rule    = "all_done",   # log even if upstream tasks failed
    )

    # ── Dependency Chain ──────────────────────────────────────────────────────
    t1_check >> t2_scrape >> t3_gcs >> t4_pg >> t5_log
