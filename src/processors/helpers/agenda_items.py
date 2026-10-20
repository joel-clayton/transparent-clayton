"""Parse a meeting agenda into its ordered top-level items (TRA-176, increment 2).

The agenda PDF (via ``pdf_to_text``) lists top-level items as numbered, all-caps
lines — ``1. CALL TO ORDER AND ROLL CALL`` — with lettered ``(a)/(b)`` sub-items
beneath. The section engine needs that ordered item list to segment the transcript
and to summarize per item, so this isolates the parsing (pure, unit-testable).

Procedural items (roll call, pledge, adjournment, ...) are flagged so the engine can
give them keywords only instead of prose — the padding the whole-meeting summarizer
produced for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A top-level agenda item line: a number, a dot, then a mostly-uppercase title.
# Sub-items ``(a)`` and prose paragraphs don't match (no leading ``N.``).
_ITEM_RE = re.compile(r"^\s*(\d{1,2})\.\s+([A-Z][A-Z0-9 ,.'&()/‐-―-]{2,})\s*$")

# Lowercased substrings that mark a purely ceremonial/administrative item. Kept
# conservative — only the clearly-contentless ones — so substantive items (consent
# calendar, public comment, public hearings) are never dropped to keywords.
PROCEDURAL_KEYWORDS: tuple[str, ...] = (
    "call to order",
    "roll call",
    "pledge",
    "moment of silence",
    "adjourn",
    "closed session",
    "recess",
    "reconvene",
    "approval of minutes",
)

# Lowercase words kept lowercase when title-casing an all-caps heading.
_SMALL_WORDS = {"of", "to", "and", "on", "the", "for", "a", "an", "in", "or", "with"}


def _title_case(upper: str) -> str:
    """Readable title case from an all-caps heading, keeping small words down and
    not mangling tokens that carry their own capitalization/punctuation."""
    words = upper.split()
    out: list[str] = []
    for i, w in enumerate(words):
        low = w.lower()
        if 0 < i < len(words) - 1 and low in _SMALL_WORDS:
            out.append(low)
        else:
            out.append(low.capitalize())
    return " ".join(out)


def is_procedural(title: str) -> bool:
    """Whether an item title is a purely ceremonial/administrative step."""
    low = title.lower()
    return any(kw in low for kw in PROCEDURAL_KEYWORDS)


@dataclass(frozen=True)
class AgendaItem:
    number: int  # the agenda number as printed (1-based)
    title: str  # readable title, e.g. "Call to Order and Roll Call"
    raw: str  # the original heading text, e.g. "CALL TO ORDER AND ROLL CALL"
    procedural: bool
    body: str = ""  # the agenda text under this item (sub-items, recommendations)


def agenda_preamble(agenda_text: str) -> str:
    """The agenda text before the first numbered item — it carries the member
    roster (``Jeff Wan, Mayor`` / ``Jim Diaz, Councilmember`` ...), which the section
    engine feeds to each section so speakers are named consistently and correctly."""
    lines = agenda_text.splitlines()
    for i, line in enumerate(lines):
        if _ITEM_RE.match(line):
            return "\n".join(lines[:i]).strip()
    return agenda_text.strip()


def parse_agenda_items(agenda_text: str) -> list[AgendaItem]:
    """Ordered top-level agenda items parsed from the agenda text.

    Each item carries its ``body`` — the agenda lines between its heading and the
    next item's — which holds the lettered sub-items and staff recommendations (e.g.
    the consent calendar's resolution numbers), context the transcript alone lacks.
    Deduplicates a repeated number (a header reprinted across PDF pages), keeping the
    first occurrence, and preserves printed order. Returns ``[]`` for an empty or
    unparseable agenda so the engine can fall back to time-windowed segmentation.
    """
    lines = agenda_text.splitlines()
    # (list index in `items`, line index where this item's heading sits)
    headings: list[tuple[int, int]] = []
    items: list[AgendaItem] = []
    seen: set[int] = set()
    for line_no, line in enumerate(lines):
        m = _ITEM_RE.match(line)
        if not m:
            continue
        number = int(m.group(1))
        if number in seen:
            continue
        seen.add(number)
        raw = m.group(2).strip()
        title = _title_case(raw)
        headings.append((len(items), line_no))
        items.append(
            AgendaItem(
                number=number, title=title, raw=raw, procedural=is_procedural(title)
            )
        )

    # Slice each item's body from just after its heading to the next heading.
    with_body: list[AgendaItem] = []
    for seq, (idx, line_no) in enumerate(headings):
        end = headings[seq + 1][1] if seq + 1 < len(headings) else len(lines)
        body = "\n".join(lines[line_no + 1 : end]).strip()
        it = items[idx]
        with_body.append(
            AgendaItem(
                number=it.number,
                title=it.title,
                raw=it.raw,
                procedural=it.procedural,
                body=body,
            )
        )
    return with_body
