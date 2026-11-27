"""Enrich stage: turn a generated summary into the wiki's publishable artifact.

This stage sits between SUMMARIZE (:class:`~src.processors.rag_index.RagIndexer`,
which writes the canonical ``sections.json`` + base ``Summaries/*.md``) and
UPDATE_WIKI. It owns the wiki-refresh trigger that the summary stage used to set
directly, decoupling "generate the summary" from "publish it" (the summary stage
no longer touches the wiki). Its job is to augment each section with deep links —
starting with a "watch from here" link into the backup video at the section's
timestamp — rewriting the ``.md`` the wiki renders.

Enrichment is additive and best-effort, rebuilt from the canonical ``sections.json``
each run (so it is idempotent and re-runnable, not a cumulative edit of the ``.md``).
A meeting with no ``sections.json`` (a legacy summary) or no single-part backup video
is a clean pass-through: its base ``.md`` is left as-is and only the wiki trigger is
set — the summary is never withheld for want of links. Idempotency is a Redis ledger
(ENRICHED_KEY); the summary stage invalidates a date here when it (re)generates that
summary, so enrichment re-runs and the wiki re-renders — the churn-free trigger the
summary stage had before.

Multi-part videos (per-part offset mapping) and transcript-Doc heading anchors land
in later increments inside :meth:`process_for_date` without changing this wiring.
"""

from __future__ import annotations

import json
import os
from typing import List

from celery_app import r
from src.constants import ENRICHED_KEY, WIKI_REFRESH_KEY
from src.processors.helpers.enrich_links import assemble_enriched_markdown
from src.processors.process import Processor
from src.processors.rag_index import SECTIONS_SUFFIX, scan_summaries
from src.settings import SUMMARIES_DIR
from src.storage_layout import resolve_existing_file
from src.types import MEETING_TYPE_BY_SOURCE, JobType, SourceType


class Enricher(Processor):
    def __init__(
        self, source_type: SourceType = SourceType.CITY_COUNCIL_MEETING
    ) -> None:
        self.job_type = JobType.ENRICH
        self.source_type = source_type
        self.meeting_type = MEETING_TYPE_BY_SOURCE[source_type]
        self.redis_key = self.meeting_type.redis_key(ENRICHED_KEY)
        super().__init__()

    def gather_input_dates(self) -> List:
        """Meetings with a publishable summary on disk — section-engine
        (``sections.json``) or legacy (``.md``) alike; both give the wiki a ``.md``
        to render, and a legacy summary still wants its wiki trigger owned here."""
        indexed, legacy = scan_summaries(self.meeting_type)
        return sorted(indexed | legacy)

    def gather_output_dates(self) -> List:
        """Dates already enriched (the ledger the base ``process`` marks on success)."""
        return [
            k.decode("utf-8") if isinstance(k, bytes) else k
            for k in (r.hkeys(self.redis_key) or [])
        ]

    # --- paths (ENRICH has no job_paths entry; resolve the summary files the way
    #     the wiki does, via file_template under SUMMARIES_DIR) --------------------
    def _summary_path(self, date: str, ext: str) -> str:
        name = self.meeting_type.file_template.format(date, ext)
        return resolve_existing_file(SUMMARIES_DIR, self.meeting_type.disk_bucket, name)

    def _single_part_video_url(self, date: str) -> str | None:
        """The backup video link for a single-part meeting, else ``None``. Zero keys
        means no video was uploaded; more than one means a multi-part recording, whose
        per-part offset mapping is a later increment — so both fall back to no link."""
        pattern = f"video_link.{self.meeting_type.key}.{date}.*"
        keys = sorted(r.scan_iter(match=pattern))
        if len(keys) != 1:
            return None
        value = r.get(keys[0])
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def process_for_date(self, date: str) -> None:
        sections_path = self._summary_path(date, SECTIONS_SUFFIX)
        # Rebuild the enriched .md from the canonical sections.json whenever both a
        # sidecar and a single-part video exist. Otherwise (legacy summary, or no/
        # multi-part video) leave the base .md untouched — a clean pass-through.
        if os.path.exists(sections_path):
            with open(sections_path, encoding="utf-8") as fh:
                sections = json.load(fh).get("sections", [])
            video_url = self._single_part_video_url(date) if sections else None
            if sections and video_url:
                self._atomic_write(
                    self._summary_path(date, ".md"),
                    assemble_enriched_markdown(sections, video_url),
                )
        # Always flag the wiki to (re-)render this meeting from the .md on disk.
        r.sadd(self.meeting_type.redis_key(WIKI_REFRESH_KEY), date)

    @staticmethod
    def _atomic_write(path: str, text: str) -> None:
        tmp = f"{path}.part"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
