"""On-disk store for meeting documents (Phase 3, TRA-134).

Meeting documents (agenda packets, minutes, staff reports) are fetched from
CivicClerk and, historically, streamed straight to Google Drive with nothing kept
locally. Phase 3 makes disk the canonical store, so documents are saved here
regardless of whether Drive publishing is enabled. The Drive archiver
(:mod:`archive_docs`) uploads from these on-disk copies.

Layout mirrors the Drive folders: one folder per meeting,
``<DOCUMENTS_DIR>/<file_stub> <meeting_key>/<label>.pdf``. Idempotent by file
existence — a document already on disk is never re-fetched.
"""

import json
import logging
import os
from typing import Iterator, Mapping

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


def document_path(meeting_type: MeetingType, meeting_key: str, label: str) -> str:
    """Where a single document lives on disk. CivicClerk documents are PDFs."""
    return os.path.join(
        meeting_document_dir(meeting_type, meeting_key), f"{_safe_label(label)}.pdf"
    )


def ensure_document_on_disk(
    meeting_type: MeetingType, meeting_key: str, label: str, url: str
) -> str:
    """Return the local path for a document, downloading it first if absent.

    Idempotent: an existing file is returned untouched. The write goes through a
    ``.part`` temp file and an atomic rename so an interrupted download never
    leaves a truncated file that a later run would treat as complete.
    """
    path = document_path(meeting_type, meeting_key, label)
    if os.path.exists(path):
        return path
    response = requests.get(url, timeout=DOC_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
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
