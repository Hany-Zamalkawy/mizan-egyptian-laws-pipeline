"""
gcp_storage.py — Bronze-layer: Upload raw JSON and PDF files to GCS.

Responsibilities
────────────────
  GCSUploader.upload_json()   : serialise a Law dict → JSON → GCS
  GCSUploader.upload_pdf()    : stream a local PDF file → GCS
  GCSUploader.upload_jsonl()  : upload an entire JSONL batch file → GCS

Object naming convention
────────────────────────
  raw/json/<year>/<law_number>.json
  raw/pdf/<year>/<law_number>.pdf

Authentication
──────────────
  Set the env var GCP_KEY_PATH to the path of your Service Account JSON key,
  or place a file named gcp_key.json next to this script.
  Alternatively, on a GCP VM with a configured service account, the client
  library will pick up Application Default Credentials automatically.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

from config import GCPConfig, LogConfig

# ─── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LogConfig.LEVEL),
    format=LogConfig.FORMAT,
    datefmt=LogConfig.DATE_FORMAT,
)
logger = logging.getLogger(__name__)


# ─── GCS Uploader ───────────────────────────────────────────────────────────────
class GCSUploader:
    """
    Handles all writes to the Bronze-layer GCS bucket.

    Parameters
    ----------
    bucket_name : str
        Target GCS bucket name.  Defaults to GCPConfig.RAW_BUCKET.
    key_path : str | Path | None
        Path to a Service Account JSON key file.
        If None the client uses Application Default Credentials (ADC).
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        key_path: Optional[str | Path] = None,
    ) -> None:
        self.bucket_name = bucket_name or GCPConfig.RAW_BUCKET

        # Build the GCS client
        key_file = key_path or GCPConfig.SERVICE_ACCOUNT_KEY
        if Path(key_file).exists():
            credentials = service_account.Credentials.from_service_account_file(
                str(key_file),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            self._client = storage.Client(
                project=GCPConfig.PROJECT_ID,
                credentials=credentials,
            )
            logger.info("GCS client initialised with service account key: %s", key_file)
        else:
            # Rely on ADC (recommended on GCP VMs / Cloud Run)
            self._client = storage.Client(project=GCPConfig.PROJECT_ID)
            logger.info("GCS client initialised with Application Default Credentials.")

        self._bucket = self._client.bucket(self.bucket_name)

    # ── Internal Helpers ──────────────────────────────────────────────────────
    def _blob(self, gcs_path: str) -> storage.Blob:
        return self._bucket.blob(gcs_path)

    def _gcs_json_path(self, year: int | str, law_number: str) -> str:
        return f"{GCPConfig.RAW_JSON_PREFIX}/{year}/{law_number}.json"

    def _gcs_pdf_path(self, year: int | str, law_number: str) -> str:
        return f"{GCPConfig.RAW_PDF_PREFIX}/{year}/{law_number}.pdf"

    # ── Public API ────────────────────────────────────────────────────────────
    def upload_json(
        self,
        law_dict: dict,
        law_number: str,
        year: int,
        overwrite: bool = False,
    ) -> str:
        """
        Serialise *law_dict* to JSON and upload to GCS.

        Returns the GCS URI (gs://<bucket>/<path>).
        Raises FileExistsError if the blob already exists and overwrite=False.
        """
        gcs_path = self._gcs_json_path(year, law_number)
        blob = self._blob(gcs_path)

        if not overwrite and blob.exists():
            uri = f"gs://{self.bucket_name}/{gcs_path}"
            logger.debug("JSON already exists, skipping: %s", uri)
            raise FileExistsError(f"Blob already exists: {uri}")

        payload = json.dumps(law_dict, ensure_ascii=False, indent=2)
        blob.upload_from_string(
            payload,
            content_type="application/json; charset=utf-8",
        )
        uri = f"gs://{self.bucket_name}/{gcs_path}"
        logger.info("Uploaded JSON → %s (%d bytes)", uri, len(payload.encode()))
        return uri

    def upload_pdf(
        self,
        local_pdf_path: Path,
        law_number: str,
        year: int,
        overwrite: bool = False,
    ) -> str:
        """
        Upload a local PDF file to GCS.

        Returns the GCS URI.
        """
        if not local_pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {local_pdf_path}")

        gcs_path = self._gcs_pdf_path(year, law_number)
        blob = self._blob(gcs_path)

        if not overwrite and blob.exists():
            uri = f"gs://{self.bucket_name}/{gcs_path}"
            logger.debug("PDF already exists, skipping: %s", uri)
            raise FileExistsError(f"Blob already exists: {uri}")

        blob.upload_from_filename(
            str(local_pdf_path),
            content_type="application/pdf",
        )
        uri = f"gs://{self.bucket_name}/{gcs_path}"
        logger.info("Uploaded PDF → %s", uri)
        return uri

    def upload_jsonl(self, local_jsonl_path: Path, gcs_subfolder: str = "batches") -> str:
        """
        Upload a full JSONL batch file to GCS under raw/json/<gcs_subfolder>/.

        Useful for uploading end-of-run bulk dumps.
        """
        if not local_jsonl_path.exists():
            raise FileNotFoundError(f"JSONL file not found: {local_jsonl_path}")

        gcs_path = f"{GCPConfig.RAW_JSON_PREFIX}/{gcs_subfolder}/{local_jsonl_path.name}"
        blob = self._blob(gcs_path)
        blob.upload_from_filename(
            str(local_jsonl_path),
            content_type="application/x-ndjson",
        )
        uri = f"gs://{self.bucket_name}/{gcs_path}"
        logger.info("Uploaded JSONL batch → %s", uri)
        return uri

    def list_uploaded_keys(self) -> set[tuple[str, str]]:
        """
        Return a set of (law_number, year) tuples already in GCS
        (based on the JSON prefix), for incremental deduplication.
        """
        prefix = GCPConfig.RAW_JSON_PREFIX + "/"
        keys: set[tuple[str, str]] = set()
        for blob in self._client.list_blobs(self.bucket_name, prefix=prefix):
            # Expected path: raw/json/<year>/<law_number>.json
            parts = blob.name.removeprefix(prefix).split("/")
            if len(parts) == 2:
                year_str, filename = parts
                law_number = filename.removesuffix(".json")
                keys.add((law_number, year_str))
        logger.info("Found %d existing JSON blobs in GCS.", len(keys))
        return keys

    def ensure_bucket_exists(self) -> None:
        """
        Create the GCS bucket if it doesn't exist.
        Idempotent — safe to call on every pipeline run.
        """
        try:
            self._client.get_bucket(self.bucket_name)
            logger.info("GCS bucket already exists: %s", self.bucket_name)
        except Exception:
            bucket = self._client.create_bucket(
                self.bucket_name,
                location="US",
            )
            logger.info("Created GCS bucket: %s", bucket.name)


# ─── Convenience function for Airflow tasks ─────────────────────────────────────
def upload_laws_to_gcs(
    laws: list[dict],
    cache_dir: Optional[Path] = None,
    overwrite: bool = False,
) -> dict[str, list[str]]:
    """
    Batch-upload a list of law dicts (JSON) and their cached PDFs to GCS.

    Parameters
    ----------
    laws       : list of law dicts (as returned by Law.to_dict())
    cache_dir  : local directory containing cached PDFs
    overwrite  : if True, re-upload existing blobs

    Returns
    -------
    dict with keys 'json_uris' and 'pdf_uris' listing uploaded GCS paths.
    """
    uploader = GCSUploader()
    uploader.ensure_bucket_exists()

    json_uris: list[str] = []
    pdf_uris: list[str] = []

    for law in laws:
        law_number = law["law_number"]
        year = law["year"]

        # ── Upload JSON ──────────────────────────────────────────────────────
        try:
            uri = uploader.upload_json(law, law_number, year, overwrite=overwrite)
            json_uris.append(uri)
        except FileExistsError:
            pass

        # ── Upload PDF (if cached locally) ───────────────────────────────────
        if cache_dir and law.get("pdf_url"):
            safe_name = f"{year}_{law_number}.pdf"
            local_pdf = cache_dir / str(year) / safe_name
            if local_pdf.exists():
                try:
                    uri = uploader.upload_pdf(local_pdf, law_number, year, overwrite=overwrite)
                    pdf_uris.append(uri)
                except FileExistsError:
                    pass

    logger.info(
        "GCS upload complete: %d JSON, %d PDF", len(json_uris), len(pdf_uris)
    )
    return {"json_uris": json_uris, "pdf_uris": pdf_uris}
