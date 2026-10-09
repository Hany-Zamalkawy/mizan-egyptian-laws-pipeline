"""
drive_uploader.py — Google Drive Sync with Deduplication and Resumable Uploads.
"""

import logging
import mimetypes
import os
from pathlib import Path
from typing import Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("drive_uploader")
SCOPES = ["https://www.googleapis.com/auth/drive"]


class GoogleDriveUploader:
    def __init__(self, credentials_file: str | Path = "credentials.json"):
        self.credentials_path = Path(credentials_file)
        self.service = self._authenticate()

    def _authenticate(self):
        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Credentials file '{self.credentials_path}' not found. "
                f"Please obtain your Service Account key and save it as credentials.json."
            )
        creds = service_account.Credentials.from_service_account_file(
            str(self.credentials_path), scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def list_files_in_folder(self, folder_id: str) -> Dict[str, str]:
        existing_files = {}
        page_token = None
        query = f"'{folder_id}' in parents and trashed = false"

        try:
            while True:
                response = (
                    self.service.files()
                    .list(
                        q=query,
                        spaces="drive",
                        fields="nextPageToken, files(id, name)",
                        pageToken=page_token,
                        pageSize=100,
                    )
                    .execute()
                )
                for file_obj in response.get("files", []):
                    existing_files[file_obj["name"]] = file_obj["id"]

                page_token = response.get("nextPageToken")
                if not page_token:
                    break
            logger.info("Found %d existing files in Drive folder [%s]", len(existing_files), folder_id)
            return existing_files
        except HttpError as err:
            logger.error("Failed to query Drive folder [%s]: %s", folder_id, err)
            raise

    @retry(
        retry=retry_if_exception_type((HttpError, ConnectionError, TimeoutError, OSError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def upload_file(
        self,
        file_path: str | Path,
        folder_id: str,
        overwrite: bool = False,
    ) -> Optional[str]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        file_name = path.name
        existing_files = self.list_files_in_folder(folder_id)

        if file_name in existing_files:
            if not overwrite:
                logger.info("File already exists in Drive (deduplication check passed): %s", file_name)
                return existing_files[file_name]
            else:
                logger.info("Overwriting Drive file: %s", file_name)
                self.service.files().delete(fileId=existing_files[file_name]).execute()

        mime_type, _ = mimetypes.guess_type(str(path))
        if not mime_type:
            mime_type = "application/octet-stream"

        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
        }

        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)

        logger.info("Streaming upload to Drive [%s]: %s (%d bytes)", folder_id, file_name, path.stat().st_size)
        drive_file = (
            self.service.files()
            .create(body=file_metadata, media_body=media, fields="id, name, webViewLink")
            .execute()
        )

        file_id = drive_file.get("id")
        logger.info("Successfully synced: %s -> Drive ID: %s", file_name, file_id)
        return file_id

    def upload_directory(
        self,
        dir_path: str | Path,
        folder_id: str,
        extensions: Optional[List[str]] = None,
        overwrite: bool = False,
    ) -> Dict[str, str]:
        directory = Path(dir_path)
        if not directory.exists():
            return {}

        uploaded_map = {}
        files = [p for p in directory.iterdir() if p.is_file()]

        if extensions:
            valid_exts = {e.lower().lstrip(".") for e in extensions}
            files = [f for f in files if f.suffix.lower().lstrip(".") in valid_exts]

        for f in files:
            try:
                fid = self.upload_file(f, folder_id, overwrite=overwrite)
                if fid:
                    uploaded_map[f.name] = fid
            except Exception as e:
                logger.error("Failed uploading %s to Drive: %s", f.name, e)

        return uploaded_map
