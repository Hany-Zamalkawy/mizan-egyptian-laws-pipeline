"""
main.py — Production Orchestration Pipeline for Egyptian Laws & Decrees.
Executes scraping, OCR, normalization, data quality validation, and Google Drive synchronization.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

from alerts import AlertManager
from drive_uploader import GoogleDriveUploader
from local_scraper import (
    PROCESSED_DATA_DIR,
    RAW_DATA_DIR,
    run_local_scraping,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
)
logger = logging.getLogger("main_pipeline")


def run_pipeline(
    limit_per_category: int = 5,
    max_pages: int = 1,
    skip_upload: bool = False,
    credentials_path: str = "credentials.json",
):
    start_time = time.time()
    alerter = AlertManager()

    logger.info("=====================================================================")
    logger.info("  🚀 Starting Production Egyptian Legal Pipeline Execution")
    logger.info("=====================================================================")

    # ── Phase 1: Scraping, OCR, Normalization, & Parsing ───────────────────────
    try:
        logger.info("[Step 1/3] Extracting legal records from sources...")
        records, saved_files = run_local_scraping(
            limit_per_category=limit_per_category,
            max_pages=max_pages,
        )
    except Exception as e:
        logger.critical("Scraping execution failed: %s", e, exc_info=True)
        alerter.notify_failure(str(e), stage="Web Scraping & Parsing")
        return

    # ── Phase 2: Data Quality Verification ─────────────────────────────────────
    logger.info("[Step 2/3] Performing Data Quality Validations...")
    passed_records = [r for r in records if r.quality_passed]
    ocr_count = sum(1 for r in records if r.is_ocr)
    total_articles = sum(len(r.articles) for r in records)

    logger.info(
        "Validation Complete: %d/%d laws passed quality checks. Total articles parsed: %d (OCR used: %d).",
        len(passed_records),
        len(records),
        total_articles,
        ocr_count,
    )

    # ── Phase 3: Google Drive Sync with Deduplication ──────────────────────────
    synced_files_count = 0
    cred_file = Path(credentials_path)

    if skip_upload:
        logger.info("[Step 3/3] Upload skipped via flag. All outputs preserved locally.")
    elif not cred_file.exists():
        logger.warning(
            "[Step 3/3] '%s' not found. Data preserved in ./raw_data and ./processed_data. "
            "To enable Google Drive upload, place your credentials.json in the project root.",
            cred_file,
        )
    else:
        logger.info("[Step 3/3] Synchronizing with Google Drive...")
        raw_folder_id = os.getenv("GDRIVE_RAW_FOLDER_ID") or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        processed_folder_id = os.getenv("GDRIVE_PROCESSED_FOLDER_ID") or os.getenv("GOOGLE_DRIVE_FOLDER_ID")

        if not raw_folder_id and not processed_folder_id:
            logger.warning(
                "Neither GDRIVE_RAW_FOLDER_ID nor GDRIVE_PROCESSED_FOLDER_ID is set in .env. Skipping Drive upload."
            )
        else:
            try:
                uploader = GoogleDriveUploader(credentials_file=cred_file)

                if raw_folder_id:
                    synced_raw = uploader.upload_directory(
                        dir_path=RAW_DATA_DIR,
                        folder_id=raw_folder_id,
                        extensions=[".pdf", ".txt"],
                    )
                    synced_files_count += len(synced_raw)

                if processed_folder_id:
                    synced_proc = uploader.upload_directory(
                        dir_path=PROCESSED_DATA_DIR,
                        folder_id=processed_folder_id,
                        extensions=[".json", ".xlsx", ".csv"],
                    )
                    synced_files_count += len(synced_proc)

                logger.info("Drive sync finished: %d files verified/uploaded.", synced_files_count)
            except Exception as drive_err:
                logger.error("Drive upload error: %s", drive_err, exc_info=True)
                alerter.notify_failure(str(drive_err), stage="Google Drive Upload")

    duration = time.time() - start_time

    # ── Phase 4: Automated Git Tracking ────────────────────────────────────────
    git_commit_hash = None
    try:
        from git_manager import GitPipelineManager
        gm = GitPipelineManager()
        if gm.is_git_repo():
            gm.stage_pipeline_files()
            git_commit_hash = gm.commit_changes(
                f"chore(pipeline): automated ingestion sync - {len(passed_records)} laws processed"
            )
            if git_commit_hash:
                logger.info("Automated Git commit created: %s", git_commit_hash)
    except Exception as git_err:
        logger.debug("Automated Git commit skipped: %s", git_err)

    # ── Final Summary Table ───────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("                    EGYPTIAN LAWS PIPELINE EXECUTION SUMMARY")
    print("=" * 80)
    print(f" • Total Laws Extracted        : {len(records)}")
    print(f" • Quality Assertions Passed   : {len(passed_records)}")
    print(f" • Total Articles Parsed       : {total_articles}")
    print(f" • Scanned Documents (OCR)     : {ocr_count}")
    print(f" • Files Synced to Google Drive: {synced_files_count}")
    print(f" • Automated Git Commit        : {git_commit_hash or 'Repository up to date'}")
    print(f" • Total Execution Time        : {duration:.2f} seconds")
    print("-" * 80)
    print(" Local Outputs:")
    for fmt, path in saved_files.items():
        print(f"   [{fmt.upper():5s}] {path}")
    print("=" * 80 + "\n")

    # Send Notification Alert
    alerter.notify_success(
        processed_count=len(passed_records),
        synced_count=synced_files_count,
        runtime_sec=duration,
    )


def main():
    parser = argparse.ArgumentParser(description="Egyptian Legal Pipeline CLI")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of records to scrape per legal category (default: 5).",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of listing pages to scan per category (default: 1).",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip Google Drive upload phase.",
    )
    parser.add_argument(
        "--credentials",
        type=str,
        default="credentials.json",
        help="Path to Google Service Account credentials.json.",
    )
    args = parser.parse_args()

    run_pipeline(
        limit_per_category=args.limit,
        max_pages=args.pages,
        skip_upload=args.skip_upload,
        credentials_path=args.credentials,
    )


if __name__ == "__main__":
    main()
