"""On-disk store for meeting documents (Phase 3, TRA-134).

Meeting documents (agenda packets, minutes, staff reports) are fetched from
CivicClerk and, historically, streamed straight to Google Drive with nothing kept
locally. Phase 3 makes disk the canonical store, so documents are saved here
regardless of whether Drive publishing is enabled. The Drive archiver
(:mod:`src.processors.upload_docs`) uploads from these on-disk copies.

Layout mirrors the Drive folders: one folder per meeting,
``<DOCUMENTS_DIR>/<file_stub> <meeting_key>/<label><ext>``, where the extension is
derived from the download's Content-Type (most are PDFs, but the type is not
assumed). Idempotent by file existence — a document already on disk is never
re-fetched.
"""

import json
import logging
import mimetypes
import os
from typing import Iterator, Mapping
from urllib.parse import urlparse

import requests

from celery_app import r
from src.constants import DETAIL_KEY
from src.meeting_types import MeetingType
from src.scrapers.models import PipelineClass
from src.settings import DOCUMENTS_DIR

logger = logging.getLogger(__name__)

DOC_DOWNLOAD_TIMEOUT = 30  # seconds
# Meetings whose documents are worth persisting: those that reach the wiki.
ARCHIVABLE_PIPELINE_CLASSES = {
    PipelineClass.FULL.value,
    PipelineClass.DOCS_ONLY.value,
}


def docs_to_archive(detail: Mapping[str, object]) -> dict[str, str]:
    """The ``{label: source_url}`` documents worth persisting for a meeting.

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


def _safe_label(label: str) -> str:
    """A filesystem-safe file stem for a document label (path separators out)."""
    return label.replace(os.sep, "-").replace(":", "-").strip()


def meeting_document_dir(meeting_type: MeetingType, meeting_key: str) -> str:
    """The per-meeting folder, mirroring the Drive meeting folder name."""
    return os.path.join(DOCUMENTS_DIR, f"{meeting_type.file_stub} {meeting_key}")


def _extension_for(content_type: str | None, url: str) -> str:
    """Pick a file extension from the download's Content-Type, falling back to the
    URL's extension, then ``.pdf`` (CivicClerk documents are overwhelmingly PDFs).
    """
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    url_ext = os.path.splitext(urlparse(url).path)[1]
    if url_ext:
        return url_ext
    return ".pdf"


def find_document(
    meeting_type: MeetingType, meeting_key: str, label: str
) -> str | None:
    """The on-disk path for a document if it has already been saved, else None.

    Matched by the sanitized label stem plus a single extension, since the
    extension is content-derived and not known ahead of the download.
    """
    directory = meeting_document_dir(meeting_type, meeting_key)
    if not os.path.isdir(directory):
        return None
    stem = _safe_label(label)
    for name in os.listdir(directory):
        rest = name[len(stem) :]
        if name.startswith(f"{stem}.") and "." not in rest[1:]:
            return os.path.join(directory, name)
    return None


def document_mimetype(path: str) -> str:
    """The MIME type to upload a saved document with, from its extension."""
    return mimetypes.guess_type(path)[0] or "application/pdf"


def ensure_document_on_disk(
    meeting_type: MeetingType, meeting_key: str, label: str, url: str
) -> str:
    """Return the local path for a document, downloading it first if absent.

    Idempotent: an already-saved file is returned untouched. The extension is
    taken from the download's Content-Type so a non-PDF document is stored (and
    later uploaded) with its true type. The write goes through a ``.part`` temp
    file and an atomic rename so an interrupted download never leaves a truncated
    file that a later run would treat as complete.
    """
    existing = find_document(meeting_type, meeting_key, label)
    if existing:
        return existing
    response = requests.get(url, timeout=DOC_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    ext = _extension_for(response.headers.get("Content-Type"), url)
    path = os.path.join(
        meeting_document_dir(meeting_type, meeting_key), f"{_safe_label(label)}{ext}"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.part"
    with open(tmp, "wb") as handle:
        handle.write(response.content)
    os.replace(tmp, path)
    return path


def meetings_with_documents(
    meeting_type: MeetingType,
) -> Iterator[tuple[str, dict]]:
    """Yield ``(meeting_key, detail)`` for this type's meetings that have
    documents worth persisting."""
    for _field, raw in (r.hgetall(meeting_type.redis_key(DETAIL_KEY)) or {}).items():
        try:
            detail = json.loads(raw.decode("utf-8"))
        except (ValueError, AttributeError):
            continue
        key = detail.get("key")
        if not key or detail.get("pipeline_class") not in ARCHIVABLE_PIPELINE_CLASSES:
            continue
        if docs_to_archive(detail):
            yield key, detail
