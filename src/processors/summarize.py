"""Generate an LLM summary per meeting (TRA-176).

A processing stage, sibling to Transcribe: it synthesizes the meeting's transcript
with its agenda and agenda packet into a per-agenda-section summary, via the local
Ollama ``meeting-summarizer`` model (built from Modelfile-Summarizer). Output is one
``.md`` file per meeting under ``Summaries/``, so the stage is idempotent (a meeting
with a summary on disk is skipped) and floor-aware like the others.

Existing (pre-stage) summaries are left untouched and recorded in a per-type
``legacy_summary`` set, so they can be selectively regenerated later if the new
output proves higher quality (TRA-176).
"""

from __future__ import annotations

import logging
from typing import List

from celery_app import r
from src.constants import LEGACY_SUMMARY_KEY, SUMMARIZED_KEY
from src.processors.constants import EARLIEST
from src.processors.helpers.document_store import find_document
from src.processors.helpers.ollama_client import generate
from src.processors.helpers.pdf_text import pdf_to_text
from src.processors.process import Processor
from src.settings import SUMMARIES_DIR, SUMMARIZER_MODEL, TRANSCRIBED_DIR
from src.types import MEETING_TYPE_BY_SOURCE, JobType, SourceType
from datetime import datetime

logger = logging.getLogger(__name__)

# Labels the agenda / packet are stored under on disk, and the section headers the
# Modelfile's SYSTEM prompt expects for its three inputs.
AGENDA_LABEL = "Agenda"
PACKET_LABEL = "Agenda Packet"


def _build_prompt(agenda: str, packet: str, transcript: str) -> str:
    """Assemble the three labeled inputs the summarizer model expects."""
    return (
        f"### MEETING AGENDA\n{agenda or '(none provided)'}\n\n"
        f"### AGENDA PACKET\n{packet or '(none provided)'}\n\n"
        f"### MEETING TRANSCRIPT\n{transcript}"
    )


class Summarizer(Processor):
    def __init__(
        self, source_type: SourceType = SourceType.CITY_COUNCIL_MEETING
    ) -> None:
        self.input_job_type = JobType.TRANSCRIBE_AUDIO
        self.job_type = JobType.SUMMARIZE
        self.source_type = source_type
        self.meeting_type = MEETING_TYPE_BY_SOURCE[source_type]
        self.redis_key = self.meeting_type.redis_key(SUMMARIZED_KEY)
        super().__init__()

    def gather_input_dates(self) -> List:
        # A summary needs a transcript; the agenda/packet are optional enrichment.
        return self.gather_dates(TRANSCRIBED_DIR)

    def gather_output_dates(self) -> List:
        return self.gather_dates(SUMMARIES_DIR)

    def _record_legacy_summaries(self) -> None:
        """Flag summaries that exist on disk but this stage never generated (an
        older/different generator) so they can be regenerated later by choice."""
        generated = {
            k.decode("utf-8") if isinstance(k, bytes) else k
            for k in (r.hkeys(self.redis_key) or [])
        }
        legacy = set(self.gather_dates(SUMMARIES_DIR)) - generated
        if legacy:
            r.sadd(self.meeting_type.redis_key(LEGACY_SUMMARY_KEY), *legacy)

    def process(self, floor: datetime = EARLIEST) -> None:
        # Record pre-existing summaries as legacy before generating any new ones.
        self._record_legacy_summaries()
        super().process(floor)

    def process_for_date(self, date: str) -> None:
        transcript_path = self.construct_filepath_for_date(date, self.input_job_type)
        with open(transcript_path, encoding="utf-8") as handle:
            transcript = handle.read()

        agenda_path = find_document(self.meeting_type, date, AGENDA_LABEL)
        packet_path = find_document(self.meeting_type, date, PACKET_LABEL)
        agenda = pdf_to_text(agenda_path) if agenda_path else ""
        packet = pdf_to_text(packet_path) if packet_path else ""

        self.logger.info("Summarizing %s", date)
        summary = generate(SUMMARIZER_MODEL, _build_prompt(agenda, packet, transcript))
        if not summary.strip():
            raise Exception(f"Empty summary returned for {date}")

        out_path = self.construct_filepath_for_date(date)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(summary)
