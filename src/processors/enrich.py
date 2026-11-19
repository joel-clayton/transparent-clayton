"""Enrich stage: turn a generated summary into the wiki's publishable artifact.

This stage sits between SUMMARIZE (:class:`~src.processors.rag_index.RagIndexer`,
which writes the canonical ``sections.json`` + base ``Summaries/*.md``) and
UPDATE_WIKI. It owns the wiki-refresh trigger that the summary stage used to set
directly, decoupling "generate the summary" from "publish it" (the summary stage
no longer touches the wiki). Its eventual job is to augment each section with deep
links — into the transcript Google Doc at the section's timestamp, and to the
backup video at the derived offset — writing the enriched ``.md`` the wiki renders.

Increment A (this change) is a behaviour-preserving pass-through: the base ``.md``
the summary stage already wrote *is* the publishable artifact, so enrichment only
flags the meeting for wiki re-render and records itself done. Link generation lands
in a later increment inside :meth:`Enricher.process_for_date` without changing this
wiring. Idempotency is a Redis ledger (ENRICHED_KEY); the summary stage invalidates
a date here when it (re)generates that summary, so enrichment re-runs and the wiki
re-renders — exactly the churn-free trigger the summary stage had before.
"""

from __future__ import annotations

from typing import List

from celery_app import r
from src.constants import ENRICHED_KEY, WIKI_REFRESH_KEY
from src.processors.process import Processor
from src.processors.rag_index import scan_summaries
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

    def process_for_date(self, date: str) -> None:
        # Increment A — pass-through: the summary stage already wrote the base .md
        # that the wiki consumes, so the only work is to flag the wiki to (re-)render
        # this meeting from it. Link generation will be added here later, producing an
        # enriched .md in place before this flag is set; the wiring does not change.
        r.sadd(self.meeting_type.redis_key(WIKI_REFRESH_KEY), date)
