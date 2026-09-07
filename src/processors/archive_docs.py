"""Archive meeting documents to Google Drive for durable, shareable links.

Documents scraped from CivicClerk are signed blob URLs that expire (~1 week), so
the wiki can't safely link to them directly. This downloads each meeting's
documents and re-uploads them to a per-meeting Drive folder, recording the
durable ``webViewLink`` per document. The wiki then links to those copies.

Applies to any meeting that has documents (FULL or DOCS_ONLY). Reuses the
headless Drive auth (:mod:`src.processors.google_auth`) and the transcript
uploader's Drive client/token, so archival shares one Drive consent with
transcripts.

Runs unattended only once the OAuth consent screen is published (see
``google_auth``). Needs a Drive parent folder — ``DOCS_DRIVE_PARENT_ID``
(env-overridable), defaulting to the existing City Council meetings folder.
"""

import io
import json
import logging
import os
from typing import Mapping, cast

import requests
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from celery_app import r
from src.constants import (
    CC_MTG_PARENT_FOLDER_ID,
    DETAIL_KEY,
    DOCS_ARCHIVED_KEY,
)
from src.meeting_types import CITY_COUNCIL, MeetingType
from src.processors.google_auth import load_credentials
from src.processors.upload_transcript import (
    DESKTOP_APP_CLIENT_SECRET,
    DRIVE_TOKEN_FILE,
    SCOPES,
)
from src.scrapers.models import PipelineClass

logger = logging.getLogger(__name__)

DOCS_DRIVE_PARENT_ID = os.environ.get("DOCS_DRIVE_PARENT_ID") or CC_MTG_PARENT_FOLDER_ID
DOC_DOWNLOAD_TIMEOUT = 30  # seconds
_ARCHIVABLE = {PipelineClass.FULL.value, PipelineClass.DOCS_ONLY.value}


def docs_to_archive(detail: Mapping[str, object]) -> dict[str, str]:
    """The ``{label: source_url}`` documents worth archiving for a meeting.

    Pure (no I/O) so the selection logic is unit-testable.
    """
    docs: dict[str, str] = {}
    agenda_packet = detail.get("agenda_packet")
    if agenda_packet:
        docs["Agenda Packet"] = str(agenda_packet)
    minutes = detail.get("minutes_and_supplemental_materials")
    if isinstance(minutes, dict):
        for label, url in minutes.items():
            if url:
                docs[str(label)] = str(url)
    return docs


class DocumentArchiver:
    def __init__(self, meeting_type: MeetingType = CITY_COUNCIL) -> None:
        self.meeting_type = meeting_type
        self.logger = logging.getLogger(f"{__name__}::DocumentArchiver")
        credentials = load_credentials(
            scopes=SCOPES,
            client_secret_path=DESKTOP_APP_CLIENT_SECRET,
            token_path=DRIVE_TOKEN_FILE,
        )
        self.service = build("drive", "v3", credentials=credentials)

    def process(self) -> None:
        for meeting_key, detail in self._unarchived_meetings():
            try:
                self._archive_meeting(meeting_key, detail)
            except Exception as exc:
                # One meeting's failure must not abort the rest; it stays out of
                # docs_archived and is retried next run.
                self.logger.error(
                    "Failed to archive documents for %s: %s", meeting_key, exc
                )

    def _unarchived_meetings(self) -> list[tuple[str, dict]]:
        archived = {
            m.decode("utf-8") if isinstance(m, bytes) else m
            for m in (
                r.smembers(self.meeting_type.redis_key(DOCS_ARCHIVED_KEY)) or set()
            )
        }
        out: list[tuple[str, dict]] = []
        for _field, raw in (
            r.hgetall(self.meeting_type.redis_key(DETAIL_KEY)) or {}
        ).items():
            try:
                detail = json.loads(raw.decode("utf-8"))
            except (ValueError, AttributeError):
                continue
            key = detail.get("key")
            if not key or key in archived:
                continue
            if detail.get("pipeline_class") not in _ARCHIVABLE:
                continue
            if docs_to_archive(detail):
                out.append((key, detail))
        return out

    def _archive_meeting(self, meeting_key: str, detail: dict) -> None:
        folder_id = self._ensure_meeting_folder(meeting_key)
        links: dict[str, str] = {}
        for label, url in docs_to_archive(detail).items():
            link = self._archive_one(folder_id, label, url)
            if link:
                links[label] = link
        if links:
            r.hset(
                self.meeting_type.doc_link_key_template.format(meeting_key=meeting_key),
                mapping=cast("Mapping[str | bytes, str]", links),
            )
        # Mark archived only after the uploads succeeded (a raise above skips
        # this, so the meeting is retried next run).
        r.sadd(self.meeting_type.redis_key(DOCS_ARCHIVED_KEY), meeting_key)
        self.logger.info("Archived %d document(s) for %s", len(links), meeting_key)

    def _archive_one(self, folder_id: str, label: str, url: str) -> str:
        # Idempotent: if a retry already uploaded this doc, reuse it.
        existing = self._find_file(folder_id, label)
        if existing:
            return existing
        response = requests.get(url, timeout=DOC_DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        media = MediaIoBaseUpload(
            io.BytesIO(response.content),
            mimetype=response.headers.get("Content-Type") or "application/pdf",
            resumable=True,
        )
        created = (
            self.service.files()
            .create(
                body={"name": label, "parents": [folder_id]},
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return created.get("webViewLink", "")

    def _ensure_meeting_folder(self, meeting_key: str) -> str:
        name = f"{self.meeting_type.file_stub} {meeting_key}"
        existing = self._find_folder(name)
        if existing:
            return existing
        folder = (
            self.service.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [DOCS_DRIVE_PARENT_ID],
                },
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return folder["id"]

    def _find_folder(self, name: str) -> str | None:
        query = (
            f"name = '{name}' and '{DOCS_DRIVE_PARENT_ID}' in parents and "
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        results = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id)", supportsAllDrives=True)
            .execute()
        )
        folders = results.get("files", [])
        return folders[0]["id"] if folders else None

    def _find_file(self, folder_id: str, name: str) -> str | None:
        query = f"name = '{name}' and '{folder_id}' in parents and trashed = false"
        results = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(webViewLink)",
                supportsAllDrives=True,
            )
            .execute()
        )
        files = results.get("files", [])
        return files[0].get("webViewLink", "") if files else None
