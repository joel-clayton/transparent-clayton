"""Fetch each meeting's documents to disk (Phase 3, TRA-134).

This is an always-on pipeline stage (independent of any publishing destination):
it saves agenda packets, minutes, and staff reports under ``DOCUMENTS_DIR`` so the
on-disk store holds every wiki-linked artifact even on a disk-only install. The
Google Drive archiver then uploads from these copies.
"""

import logging

from src.meeting_types import CITY_COUNCIL, MeetingType
from src.processors.helpers.document_store import (
    docs_to_archive,
    ensure_document_on_disk,
    meetings_with_documents,
)
from src.scrapers.alerting import AlertLevel, alert


class DocumentDownloader:
    def __init__(self, meeting_type: MeetingType = CITY_COUNCIL) -> None:
        self.meeting_type = meeting_type
        self.logger = logging.getLogger(f"{__name__}::DocumentDownloader")

    def process(self) -> None:
        failures: list[str] = []
        for meeting_key, detail in meetings_with_documents(self.meeting_type):
            for label, url in docs_to_archive(detail).items():
                try:
                    ensure_document_on_disk(self.meeting_type, meeting_key, label, url)
                except Exception as exc:
                    # One document's failure must not block the rest; it stays
                    # absent and is retried next run.
                    self.logger.error(
                        "Failed to save document %r for %s: %s",
                        label,
                        meeting_key,
                        exc,
                    )
                    failures.append(f"{meeting_key} / {label}: {exc}")

        if failures:
            # Surface a batched, actionable alert like the other pipeline stages
            # (TRA-126), so persistently failing document downloads are visible
            # rather than only logged.
            alert(
                AlertLevel.ACTIONABLE,
                f"{len(failures)} {self.meeting_type.display_name} document(s) "
                "failed to save this run:\n- " + "\n- ".join(failures),
            )
