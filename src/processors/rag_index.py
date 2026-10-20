"""Section-engine stage: per-agenda-item summaries + RAG index (TRA-176, increment 2).

Supersedes the whole-meeting :class:`~src.processors.summarize.Summarizer`. For each
meeting it: parses the agenda into ordered items, segments the transcript into per-
item spans, summarizes each reached non-procedural item from *only* its own span +
agenda text (no context overflow, grounded), writes the canonical ``sections.json``
and an assembled ``Summaries/*.md`` for the wiki, and embeds the section summaries
into the sqlite-vec store with provenance.

It occupies the existing ``SUMMARIZE`` stage slot (same ``.md`` output and wiki
seam), but idempotency is keyed on the per-meeting ``sections.json`` sidecar, and
pre-existing (legacy) summaries are left untouched and recorded for optional later
regeneration — the same contract the Summarizer had.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List

from celery_app import r
from src.constants import LEGACY_SUMMARY_KEY, SUMMARIZED_KEY, WIKI_REFRESH_KEY
from src.processors.constants import EARLIEST
from src.processors.helpers.agenda_items import (
    AgendaItem,
    agenda_preamble,
    parse_agenda_items,
)
from src.processors.helpers.document_store import find_document
from src.processors.helpers.embeddings import embed_document
from src.processors.helpers.ollama_client import model_digest
from src.processors.helpers.pdf_text import pdf_to_text
from src.processors.helpers.rag_store import (
    SectionRow,
    connect,
    replace_meeting_sections,
    section_row_to_json,
)
from src.processors.helpers.section_summary import (
    NOT_TAKEN_UP,
    build_speaker_key,
    extract_keywords,
    procedural_line,
    summarize_section,
)
from src.processors.helpers.segmentation import Segment, segment_meeting
from src.processors.helpers.transcript import (
    Utterance,
    parse_utterances,
    render_span,
    utterances_in_span,
)
from src.processors.process import Processor
from src.processors.summarize import AGENDA_LABEL
from src.settings import (
    EMBEDDING_MODEL,
    KEYWORD_MODEL,
    PROMPT_VERSION,
    SECTION_SUMMARY_MODEL,
    SEGMENTER_MODEL,
    SUMMARIES_DIR,
    TRANSCRIBED_DIR,
)
from src.storage_layout import iter_stage_files
from src.types import MEETING_TYPE_BY_SOURCE, JobType, SourceType
from src.util import get_datetime_string_from_string

SECTIONS_SUFFIX = ".sections.json"
_SUMMARY_EXTS = (".md", ".txt")
_SEED = 42
# Tokens per "Part N" chunk when summarizing a meeting with no usable agenda
# segmentation — kept well within the section model's context so nothing truncates.
FALLBACK_WINDOW_TOKENS = 8000


class RagIndexer(Processor):
    def __init__(
        self, source_type: SourceType = SourceType.CITY_COUNCIL_MEETING
    ) -> None:
        self.input_job_type = JobType.TRANSCRIBE_AUDIO
        self.job_type = JobType.SUMMARIZE
        self.source_type = source_type
        self.meeting_type = MEETING_TYPE_BY_SOURCE[source_type]
        self.redis_key = self.meeting_type.redis_key(SUMMARIZED_KEY)
        super().__init__()

    # --- date discovery ------------------------------------------------------
    def gather_input_dates(self) -> List:
        return self.gather_dates(TRANSCRIBED_DIR)

    def _scan_summaries(self) -> tuple[set[str], set[str]]:
        """Scan the summaries bucket once, returning ``(indexed, legacy)`` date sets:
        ``indexed`` = dates with a ``sections.json`` (produced by this stage);
        ``legacy`` = dates with a ``.md``/``.txt`` summary but no ``sections.json``
        (a pre-existing/older summary this stage must not overwrite)."""
        indexed: set[str] = set()
        summaries: set[str] = set()
        bucket = self.meeting_type.disk_bucket
        stub = self.meeting_type.file_stub
        for full in iter_stage_files(SUMMARIES_DIR, bucket):
            name = os.path.basename(full)
            if stub not in name:
                continue
            key = get_datetime_string_from_string(name)
            if not key:
                continue
            if name.endswith(SECTIONS_SUFFIX):
                indexed.add(key)
            elif name.endswith(_SUMMARY_EXTS):
                summaries.add(key)
        return indexed, summaries - indexed

    def gather_output_dates(self) -> List:
        indexed, legacy = self._scan_summaries()
        return sorted(indexed | legacy)

    def _record_legacy_summaries(self) -> None:
        """Flag pre-existing summaries (no ``sections.json``) so they can be
        regenerated later by choice, mirroring the Summarizer's contract."""
        _indexed, legacy = self._scan_summaries()
        if legacy:
            r.sadd(self.meeting_type.redis_key(LEGACY_SUMMARY_KEY), *legacy)

    def _heal_missing_markdown(self) -> None:
        """Rebuild a wiki ``.md`` from its ``sections.json`` when the ``.md`` is
        missing — e.g. a crash between writing the sidecar and the ``.md``, or a
        deleted ``.md``. Cheap and model-free: ``sections.json`` is canonical and the
        ``.md`` is a pure function of it, so this closes the gap where a meeting
        would stay ``indexed`` with a permanently blank wiki summary."""
        indexed, _legacy = self._scan_summaries()
        for date in indexed:
            md_path = self.construct_filepath_for_date(date)
            sj_path = self._sections_json_path(date)
            if os.path.exists(md_path) or not os.path.exists(sj_path):
                continue
            with open(sj_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            rows = [
                {
                    "number": s["item_index"],
                    "title": s["agenda_item"],
                    "summary": s["summary"],
                    "keywords": s.get("keywords") or [],
                }
                for s in payload.get("sections", [])
            ]
            if rows:
                self._atomic_write(md_path, self._assemble_markdown(rows))
                self._mark_for_wiki_refresh(date)
                self.logger.info("Rebuilt missing .md for %s from sections.json", date)

    def _mark_for_wiki_refresh(self, date: str) -> None:
        """Flag a meeting for wiki re-render after its summary ``.md`` is written.

        The wiki fills its AI-summary section from this ``.md`` (else ``TBD``) and
        only re-renders an *existing* page entry when WIKI_REFRESH_KEY is set. Without
        this flag, a meeting whose entry was published before its summary existed
        (e.g. a summarize failure on the first run) would stay ``TBD`` forever. It is
        a no-op for a meeting not yet on the page (the normal single-run cascade),
        and summarize is idempotent, so the flag is set only when a summary is
        actually (re)generated — no per-run churn (TRA-178)."""
        r.sadd(self.meeting_type.redis_key(WIKI_REFRESH_KEY), date)

    def process(self, floor: datetime = EARLIEST) -> None:
        self._record_legacy_summaries()
        self._heal_missing_markdown()
        super().process(floor)

    # --- paths ---------------------------------------------------------------
    def _sections_json_path(self, date: str) -> str:
        md_path = self.construct_filepath_for_date(date)  # the .md sidecar's base
        return (
            md_path[: -len(".md")] + SECTIONS_SUFFIX
            if md_path.endswith(".md")
            else md_path + SECTIONS_SUFFIX
        )

    # --- the pipeline --------------------------------------------------------
    def _build_sections(
        self,
        segments: list[Segment],
        items_by_num: dict[int, AgendaItem],
        context: str = "",
    ) -> list[dict]:
        """Turn segments into section dicts (summary + keywords), one LLM pass each
        for reached non-procedural items; procedural/not-reached are cheap.

        ``context`` (roster + roll call) is fed to each section so speakers are
        named consistently from the transcript's anonymous 'Speaker X' labels.
        """
        sections: list[dict] = []
        for seg in segments:
            item = items_by_num[seg.number]
            embed = False
            keywords: list[str] = []
            if not seg.reached:
                summary = NOT_TAKEN_UP
            elif seg.procedural:
                summary = procedural_line(item, seg.t_start)
                keywords = []
            else:
                summary = summarize_section(
                    item,
                    render_span(seg.utterances),
                    model=SECTION_SUMMARY_MODEL,
                    context=context,
                )
                keywords = extract_keywords(summary, model=KEYWORD_MODEL)
                embed = bool(summary.strip())
            sections.append(
                {
                    "number": seg.number,
                    "title": seg.title,
                    "procedural": seg.procedural,
                    "reached": seg.reached,
                    "t_start": seg.t_start,
                    "t_end": seg.t_end,
                    "summary": summary,
                    "keywords": keywords,
                    "embed": embed,
                }
            )
        return sections

    def _assemble_markdown(self, sections: list[dict]) -> str:
        """Stitch section summaries into the per-meeting wiki ``.md`` — an honest
        agenda walkthrough: prose for substantive items, a line for procedural ones,
        an explicit note for items not taken up. No padding, no meta-commentary."""
        blocks: list[str] = []
        for s in sections:
            block = [f"## Item {s['number']}: {s['title']}", "", s["summary"]]
            if s["keywords"]:
                block += ["", f"**Keywords:** {', '.join(s['keywords'])}"]
            blocks.append("\n".join(block))
        return "\n\n".join(blocks) + "\n"

    def _fallback_segments(self, utterances: list[Utterance]) -> list[Segment]:
        """Bounded time-windowed pseudo-sections for a meeting with no usable agenda
        segmentation. Splits the transcript into ~FALLBACK_WINDOW_TOKENS chunks so
        each is summarized within the section model's context (no truncation) and the
        whole meeting is captured; a single-chunk meeting is one 'Meeting' section."""
        budget = FALLBACK_WINDOW_TOKENS * 4  # chars
        chunks: list[list[Utterance]] = []
        cur: list[Utterance] = []
        size = 0
        for u in utterances:
            ulen = len(u.text) + len(u.speaker) + 16
            if cur and size + ulen > budget:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(u)
            size += ulen
        if cur:
            chunks.append(cur)
        single = len(chunks) == 1
        return [
            Segment(
                number=i,
                title="Meeting" if single else f"Part {i}",
                procedural=False,
                reached=True,
                t_start=chunk[0].start,
                t_end=chunk[-1].end,
                utterances=chunk,
            )
            for i, chunk in enumerate(chunks, start=1)
        ]

    def process_for_date(self, date: str) -> None:
        transcript_path = self.construct_filepath_for_date(date, self.input_job_type)
        with open(transcript_path, encoding="utf-8") as handle:
            transcript = handle.read()
        utterances = parse_utterances(transcript)
        if not utterances:
            raise Exception(f"No parseable transcript utterances for {date}")

        agenda_path = find_document(self.meeting_type, date, AGENDA_LABEL)
        agenda_text = pdf_to_text(agenda_path) if agenda_path else ""
        items = parse_agenda_items(agenda_text)

        self.logger.info("Indexing %s (%d agenda items)", date, len(items))
        segments = (
            segment_meeting(items, utterances, model=SEGMENTER_MODEL) if items else []
        )
        if not any(seg.reached for seg in segments):
            # No parseable agenda, or segmentation detected no boundaries at all (no
            # chair cues) — so no agenda item was captured. Rather than emit a
            # meeting where every item reads "not taken up" (losing the content) or
            # feed the whole transcript to one call (which would truncate a long
            # meeting at num_ctx), split it into bounded time-windowed "Part N"
            # sections so the full meeting is summarized and indexed.
            segments = self._fallback_segments(utterances)
            items = [
                AgendaItem(
                    number=s.number,
                    title=s.title,
                    raw=s.title.upper(),
                    procedural=False,
                )
                for s in segments
            ]
        items_by_num = {it.number: it for it in items}
        # Per-meeting context so each isolated section can name speakers: the agenda
        # roster plus an explicit SPEAKER KEY built once from the roll call (the
        # opening minutes), which maps 'Speaker X' labels to members.
        roster = agenda_preamble(agenda_text)
        opening = render_span(
            utterances_in_span(
                utterances, utterances[0].start, utterances[0].start + 180
            )
        )
        speaker_key = build_speaker_key(roster, opening, model=KEYWORD_MODEL)
        context = "\n\n".join(
            part
            for part in (
                f"MEMBER ROSTER:\n{roster}" if roster else "",
                f"SPEAKER KEY:\n{speaker_key}" if speaker_key else "",
            )
            if part
        )
        sections = self._build_sections(segments, items_by_num, context)

        # Provenance (best-effort; digests pin the exact model blobs used).
        now = datetime.now(timezone.utc).isoformat()
        rows = self._section_rows(date, sections, now)

        # Write order matters for crash-safety: store, then the canonical
        # sections.json sidecar, then the wiki .md LAST. Because the .md is written
        # only after sections.json exists, our pipeline can never leave a .md
        # without a sections.json — so _scan_summaries only ever sees that state for
        # a genuine pre-existing (legacy) summary, never a crashed partial. A crash
        # before sections.json leaves nothing to skip on (the meeting reprocesses);
        # the store write is idempotent (replace-per-meeting) on that retry.
        conn = connect()
        try:
            replace_meeting_sections(conn, self.meeting_type.disk_bucket, date, rows)
        finally:
            conn.close()

        payload = {
            "meeting_type": self.meeting_type.disk_bucket,
            "meeting_key": date,
            "generated_at": now,
            "sections": [section_row_to_json(row) for row in rows],
        }
        self._atomic_write(
            self._sections_json_path(date),
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        self._atomic_write(
            self.construct_filepath_for_date(date), self._assemble_markdown(sections)
        )
        self._mark_for_wiki_refresh(date)

    @staticmethod
    def _atomic_write(path: str, text: str) -> None:
        tmp = f"{path}.part"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def _section_rows(
        self, date: str, sections: list[dict], created_at: str
    ) -> list[SectionRow]:
        """Build store rows, embedding the substantive section summaries."""
        sum_digest = model_digest(SECTION_SUMMARY_MODEL)
        emb_digest = model_digest(EMBEDDING_MODEL)
        transcript_name = os.path.basename(
            self.construct_filepath_for_date(date, self.input_job_type)
        )
        rows: list[SectionRow] = []
        for s in sections:
            embedding = embed_document(s["summary"]) if s.get("embed") else None
            rows.append(
                SectionRow(
                    meeting_type=self.meeting_type.disk_bucket,
                    meeting_key=date,
                    item_index=s["number"],
                    agenda_item=s["title"],
                    summary=s["summary"],
                    t_start=s["t_start"],
                    t_end=s["t_end"],
                    procedural=s["procedural"],
                    keywords=s["keywords"],
                    transcript_ref=transcript_name,
                    segmenter_model=SEGMENTER_MODEL,
                    summary_model=SECTION_SUMMARY_MODEL,
                    summary_model_digest=sum_digest,
                    embed_model=EMBEDDING_MODEL if embedding else None,
                    embed_model_digest=emb_digest if embedding else None,
                    prompt_version=PROMPT_VERSION,
                    seed=_SEED,
                    created_at=created_at,
                    embedding=embedding,
                )
            )
        return rows
