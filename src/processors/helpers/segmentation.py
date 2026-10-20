"""Segment a transcript into per-agenda-item spans (TRA-176, increment 2).

The hard part of the section engine: transcripts don't cleanly announce agenda
boundaries. The strategy here is **LLM boundary detection** — give the model the
ordered agenda items and the timestamped transcript and ask where each item's
discussion *begins*; spans are then ``[start_i, start_of_next_reached_item)``.
Chair cues ("next item", "moving to item 3") make this reliable, and it is run in
sequential windows so an arbitrarily long transcript stays within context.

Kept behind ``segment_meeting`` so the strategy (windowed tagging, chair-cue
heuristics, embedding alignment) can evolve without touching the rest of the stage.
Deterministic: ``think=False`` + ``seed`` + ``temperature 0`` + JSON-schema output.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.processors.helpers.agenda_items import AgendaItem
from src.processors.helpers.ollama_client import generate
from src.processors.helpers.transcript import (
    Utterance,
    hms_to_seconds,
    seconds_to_hms,
    utterances_in_span,
)

logger = logging.getLogger(__name__)

# Deterministic classification settings (see module docstring).
SEG_SEED = 42
# Transcript tokens per window (~4 chars/token); well under a 32k ctx with room for
# the agenda list, the JSON schema, and the response.
WINDOW_TOKENS = 9000

_SEG_SYSTEM = (
    "You identify where each agenda item begins in a city council meeting "
    "transcript. You are given the ordered AGENDA ITEMS and a TRANSCRIPT whose "
    "lines are timestamped (h:mm:ss). For each agenda item, report the timestamp "
    "where its discussion BEGINS, using the chair's spoken cues (e.g. 'next item', "
    "'moving to item 3', 'item 4, the consent calendar'). If an item is never "
    "reached or discussed in the provided transcript, report null for it. Report "
    "each listed agenda item exactly once. Output only JSON."
)

_SEG_SCHEMA = {
    "type": "object",
    "properties": {
        "boundaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer"},
                    "start": {"type": ["string", "null"]},
                },
                "required": ["item", "start"],
            },
        }
    },
    "required": ["boundaries"],
}


@dataclass
class Segment:
    number: int
    title: str
    procedural: bool
    reached: bool  # was the item actually taken up in the transcript?
    t_start: int  # seconds; == t_end when not reached
    t_end: int
    utterances: list[Utterance]


def _render_for_model(utterances: list[Utterance]) -> str:
    """Compact timestamped transcript for the model: one line per utterance."""
    return "\n".join(
        f"({seconds_to_hms(u.start)}) [{u.speaker}] {u.text}" for u in utterances
    )


def _windows(utterances: list[Utterance], window_tokens: int) -> list[list[Utterance]]:
    """Split utterances into sequential windows of roughly ``window_tokens``."""
    windows: list[list[Utterance]] = []
    current: list[Utterance] = []
    budget = window_tokens * 4  # chars
    size = 0
    for u in utterances:
        ulen = len(u.text) + len(u.speaker) + 16
        if current and size + ulen > budget:
            windows.append(current)
            current, size = [], 0
        current.append(u)
        size += ulen
    if current:
        windows.append(current)
    return windows


def _ask_boundaries(
    items: list[AgendaItem], window: list[Utterance], model: str
) -> dict[int, int]:
    """Ask the model for ``{item_number: start_seconds}`` within one window.

    Items reported null, out of range, or not listed are omitted. Timestamps are
    clamped to the window's own time range (a hallucinated time outside it is
    dropped) so a bad value can't misplace a boundary.
    """
    agenda = "\n".join(f"{it.number}. {it.title}" for it in items)
    prompt = f"AGENDA ITEMS:\n{agenda}\n\nTRANSCRIPT:\n{_render_for_model(window)}"
    raw = generate(
        model,
        prompt,
        system=_SEG_SYSTEM,
        think=False,
        format=_SEG_SCHEMA,
        options={"seed": SEG_SEED, "temperature": 0},
    )
    lo, hi = window[0].start, window[-1].start
    valid = {it.number for it in items}
    out: dict[int, int] = {}
    try:
        boundaries = json.loads(raw).get("boundaries", [])
    except (ValueError, TypeError):
        logger.warning("Segmenter returned unparseable JSON; window skipped")
        return out
    for b in boundaries:
        number, start = b.get("item"), b.get("start")
        if number not in valid or not start:
            continue
        try:
            secs = hms_to_seconds(str(start))
        except (ValueError, AttributeError):
            continue
        if lo <= secs <= hi:
            out.setdefault(int(number), secs)
    return out


def segment_meeting(
    items: list[AgendaItem], utterances: list[Utterance], *, model: str
) -> list[Segment]:
    """Segment the transcript into one span per agenda item, in agenda order.

    Returns an empty list if there is no agenda or no transcript. Boundaries are
    forced monotonic by agenda order (a later item cannot start before an earlier
    one); an item with no detected boundary is marked ``reached=False`` with a
    zero-length span at the prior boundary, so the engine can note it was not taken
    up rather than inventing content for it.
    """
    if not items or not utterances:
        return []

    detected: dict[int, int] = {}
    for window in _windows(utterances, WINDOW_TOKENS):
        for number, secs in _ask_boundaries(items, window, model).items():
            detected.setdefault(number, secs)  # earliest window wins

    meeting_start, meeting_end = utterances[0].start, utterances[-1].end

    # Resolve a monotonic start per item in agenda order. A detected start that
    # regresses below the running floor is pulled up to the floor (keeps order);
    # an undetected item inherits the floor and is flagged not-reached.
    floor = meeting_start
    starts: list[tuple[AgendaItem, int, bool]] = []
    for it in items:
        if it.number in detected:
            start = max(detected[it.number], floor)
            starts.append((it, start, True))
            floor = start
        else:
            starts.append((it, floor, False))

    # End of each span = start of the next *reached* item, else meeting end. An
    # item whose span collapses to nothing (its boundary coincides with the next
    # item's — e.g. two items the chair ran together, or an item with no business)
    # is treated as not taken up rather than given a one-utterance sliver.
    segments: list[Segment] = []
    for i, (it, start, reached) in enumerate(starts):
        end = meeting_end
        for _it2, start2, reached2 in starts[i + 1 :]:
            if reached2:
                end = start2
                break
        empty = not reached or end <= start
        segments.append(
            Segment(
                number=it.number,
                title=it.title,
                procedural=it.procedural,
                reached=not empty,
                t_start=start,
                t_end=start if empty else end,
                utterances=[] if empty else utterances_in_span(utterances, start, end),
            )
        )
    return segments
