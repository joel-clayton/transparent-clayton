"""Upload meeting documents to Google Drive for durable, shareable links.

Documents scraped from CivicClerk are signed blob URLs that expire (~1 week), so
the wiki can't safely link to them directly. This uploads each meeting's on-disk
documents to a per-meeting Drive folder, recording the durable ``webViewLink``
per document. The wiki then links to those copies. The documents themselves are
fetched to disk by the documents-to-disk stage (see
:mod:`src.processors.download_docs`); this uploader sources from those copies.

Applies to any meeting that has documents (FULL or DOCS_ONLY). Reuses the
headless Drive auth (:mod:`src.processors.helpers.google_auth`) and the
transcript uploader's Drive client/token, so this shares one Drive consent with
transcripts.

Runs unattended only once the OAuth consent screen is published (see
``google_auth``). Per-meeting document folders are created under the meeting
type's own folder (e.g. "GHAD Meetings") in the shared Source Material parent;
setting ``DOCS_DRIVE_PARENT_ID`` overrides that with a fixed folder.
"""

import io
import json
import logging
import os
from typing import Mapping, cast

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from celery_app import r
from src.constants import (
    DETAIL_KEY,
    DOCS_ARCHIVED_KEY,
)
from src.meeting_types import CITY_COUNCIL, MeetingType
from src.processors.helpers.document_store import (
    ARCHIVABLE_PIPELINE_CLASSES as _ARCHIVABLE,
    docs_to_archive,
    document_mimetype,
    ensure_document_on_disk,
)
from src.processors.helpers.drive_folders import (
    escape_drive_query_value,
    find_or_create_type_folder,
)
from src.processors.helpers.google_auth import load_credentials
from src.processors.upload_transcript import (
    DESKTOP_APP_CLIENT_SECRET,
    DRIVE_TOKEN_FILE,
    SCOPES,
)

logger = logging.getLogger(__name__)

# ``docs_to_archive`` lives in helpers.document_store and is re-exported here so
# existing importers keep working.
__all__ = ["DocumentUploader", "docs_to_archive"]


def _drive_view_link(file_id: str) -> str:
    """The canonical Drive view link for a file id — used when a create/list
    response omits ``webViewLink``, so a reused or just-created file still yields a
    usable, reconcile-parseable link instead of an empty string."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def _file_link_or_raise(resource: dict, name: str) -> str:
    """A usable view link for a Drive file resource (found or just created): its
    ``webViewLink``, else one constructed from its id, else a loud error — never a
    falsy link (that would mark a meeting archived with an incomplete doc_link
    hash)."""
    link = resource.get("webViewLink")
    if link:
        return link
    file_id = resource.get("id")
    if file_id:
        return _drive_view_link(file_id)
    raise RuntimeError(f"Drive file {name!r} has neither id nor webViewLink")


class DocumentUploader:
    def __init__(self, meeting_type: MeetingType = CITY_COUNCIL) -> None:
        self.meeting_type = meeting_type
        self.logger = logging.getLogger(f"{__name__}::DocumentUploader")
        credentials = load_credentials(
            scopes=SCOPES,
            client_secret_path=DESKTOP_APP_CLIENT_SECRET,
            token_path=DRIVE_TOKEN_FILE,
        )
        self.service = build("drive", "v3", credentials=credentials)
        # Per-meeting document folders live under this type's own folder (e.g.
        # "GHAD Meetings") in the shared Source Material parent. An explicit
        # DOCS_DRIVE_PARENT_ID still overrides, for one-off relocations.
        self.docs_parent_id = os.environ.get(
            "DOCS_DRIVE_PARENT_ID"
        ) or find_or_create_type_folder(self.service, self.meeting_type)

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
            links[label] = self._archive_one(folder_id, meeting_key, label, url)
        if links:
            r.hset(
                self.meeting_type.doc_link_key_template.format(meeting_key=meeting_key),
                mapping=cast("Mapping[str | bytes, str]", links),
            )
        # Reaching here means every document archived: _archive_one returns a usable
        # link for each (reusing an existing file by id, or constructing one) or
        # raises — and a raise aborts above (caught in process()), leaving the
        # meeting unarchived so the next run retries. So a meeting is marked archived
        # only when its doc_link hash is complete, never with gaps (the bug TRA-154
        # set out to fix: a meeting stuck in DOCS_ARCHIVED with empty/partial links
        # that reconcile reported as MISSING_DOC forever).
        r.sadd(self.meeting_type.redis_key(DOCS_ARCHIVED_KEY), meeting_key)
        self.logger.info("Archived %d document(s) for %s", len(links), meeting_key)

    def _archive_one(
        self, folder_id: str, meeting_key: str, label: str, url: str
    ) -> str:
        # Idempotent: if a retry already uploaded this doc, reuse it (by id, so a
        # file that exists but whose listing omitted webViewLink is still reused
        # rather than re-created into a duplicate).
        existing = self._find_file(folder_id, label)
        if existing:
            return existing
        # Source from the on-disk copy (downloaded once, shared with the
        # documents-to-disk stage) rather than re-fetching from CivicClerk.
        path = ensure_document_on_disk(self.meeting_type, meeting_key, label, url)
        with open(path, "rb") as handle:
            content = handle.read()
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype=document_mimetype(path),
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
        return _file_link_or_raise(created, label)

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
                    "parents": [self.docs_parent_id],
                },
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return folder["id"]

    def _find_folder(self, name: str) -> str | None:
        query = (
            f"name = '{escape_drive_query_value(name)}' and "
            f"'{self.docs_parent_id}' in parents and "
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
        query = (
            f"name = '{escape_drive_query_value(name)}' and "
            f"'{folder_id}' in parents and trashed = false"
        )
        results = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, webViewLink)",
                supportsAllDrives=True,
            )
            .execute()
        )
        files = results.get("files", [])
        if not files:
            return None
        # Reuse by id so a retry never re-creates (duplicates) an already-uploaded
        # file; fall back to the canonical view link when the listing omits one.
        return _file_link_or_raise(files[0], name)
