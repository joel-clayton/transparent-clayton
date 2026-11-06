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
# An all-caps title may be followed by a dash and prose (``1. CALL TO ORDER AND ROLL
# CALL – Mayor Haydon.``, ``11. ADJOURNMENT - the meeting of ... was canceled.``);
# only the all-caps part is the title.
_ITEM_RE = re.compile(
    r"^\s*(\d{1,2})\.\s+([A-Z][A-Z0-9 ,.'&()/‐-―-]{2,}?)(?:\s+[‐-―-]\s+\S.*)?\s*$"
)
_ADJOURN_LINE_RE = re.compile(r"^\s*\d{1,2}\.\s+ADJOURN", re.I | re.M)
# With no adjournment line to cut at, only this much of a packet is treated as agenda.
_PACKET_AGENDA_CHARS = 15000

# Fallback for agendas whose headings are Title/sentence case instead of all-caps
# (the PDF's bold/underline styling is invisible to text extraction, so some
# meetings print every heading in mixed case). Used only when the strict pattern
# finds fewer than two items, and constrained by _loose_headings below.
_LOOSE_ITEM_RE = re.compile(r"^\s*(\d{1,2})\.\s+([A-Z][^\n]{2,}?)\s*$")
# A line that starts a sub-item, a parenthetical note, or another numbered item.
_NEW_LINE_ITEM_RE = re.compile(r"^\s*(?:[a-z]\.|\([^)]*\)|\d{1,2}\.)(?:\s|$)")
# A heading line at least this long was probably wrapped by the PDF layout, so the
# next line continues its title (a short line ends it).
_WRAP_MIN_CHARS = 55
_MAX_TITLE_LINES = 3

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


def _strict_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, number, heading text) for all-caps numbered items, first
    occurrence of each number (a header reprinted across PDF pages is ignored)."""
    seen: set[int] = set()
    out: list[tuple[int, int, str]] = []
    for line_no, line in enumerate(lines):
        m = _ITEM_RE.match(line)
        if m and int(m.group(1)) not in seen:
            seen.add(int(m.group(1)))
            out.append((line_no, int(m.group(1)), m.group(2).strip()))
    return out


def _loose_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Numbered items in any case, for agendas the strict pattern can't read.

    Safe against numbered prose elsewhere in the document (staff reports follow the
    agenda in the same PDF) because items must run 1, 2, 3, ... in order and the
    list stops at the Adjournment item. A heading the PDF wrapped onto further lines
    has them joined into its title."""
    out: list[tuple[int, int, str]] = []
    expected = 1
    for line_no, line in enumerate(lines):
        m = _LOOSE_ITEM_RE.match(line)
        if not m or int(m.group(1)) != expected:
            continue
        title = m.group(2).strip()
        last = line
        nxt = line_no + 1
        while (
            len(last.strip()) >= _WRAP_MIN_CHARS
            and len(title.split("\n")) < _MAX_TITLE_LINES
            and nxt < len(lines)
            and lines[nxt].strip()
            and not _NEW_LINE_ITEM_RE.match(lines[nxt])
        ):
            title += " " + lines[nxt].strip()
            last = lines[nxt]
            nxt += 1
        out.append((line_no, expected, title))
        expected += 1
        if "adjourn" in title.lower():
            break
    return out


def _find_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """Top-level item headings: all-caps numbered lines; if that finds fewer than two
    (a mixed-case agenda), the stricter-sequenced mixed-case reading."""
    strict = _strict_headings(lines)
    if len(strict) >= 2:
        return strict
    loose = _loose_headings(lines)
    return loose if len(loose) > len(strict) else strict


def agenda_preamble(agenda_text: str) -> str:
    """The agenda text before the first numbered item — it carries the member
    roster (``Jeff Wan, Mayor`` / ``Jim Diaz, Councilmember`` ...), which the section
    engine feeds to each section so speakers are named consistently and correctly."""
    lines = agenda_text.splitlines()
    headings = _find_headings(lines)
    if headings:
        return "\n".join(lines[: headings[0][0]]).strip()
    return agenda_text.strip()


_ROLL_START_RE = re.compile(
    r"^\s*(?:\d+\.\s*)?(?:CALL TO ORDER|ROLL CALL)", re.I | re.M
)
_NEXT_ITEM_RE = re.compile(r"^\s*\d+\.\s+\S", re.M)


def minutes_roll_call(minutes_text: str, max_chars: int = 1500) -> str:
    """The roll-call paragraph of a meeting's minutes — who was present (with
    titles) and which staff attended — e.g. ``Committee members present: Vice Mayor
    Wan and Mayor Trupiano. Staff present: City Manager, ...``. Unlike the agenda's
    printed roster (reused template text whose titles can be stale), it records who
    actually attended, so it is the better source for naming speakers. Returns ""
    when the minutes have no such paragraph."""
    start = _ROLL_START_RE.search(minutes_text)
    if not start:
        return ""
    rest = minutes_text[start.start() :]
    first_line_end = rest.find("\n")
    end = (
        _NEXT_ITEM_RE.search(rest, first_line_end + 1) if first_line_end >= 0 else None
    )
    block = " ".join((rest[: end.start()] if end else rest).split())
    return block[:max_chars] if "present" in block.lower() else ""


def agenda_from_packet(packet_text: str) -> str:
    """The agenda portion of an agenda packet, for meetings that have no separate
    agenda file. The agenda leads the packet and the staff reports follow it, so the
    text is cut after the Adjournment item (the reports' own numbered headings would
    otherwise be read as agenda items); with no such line, only the first
    ``_PACKET_AGENDA_CHARS`` characters are used."""
    match = _ADJOURN_LINE_RE.search(packet_text)
    if match:
        end = packet_text.find("\n", match.end())
        return packet_text[: end if end != -1 else len(packet_text)]
    return packet_text[:_PACKET_AGENDA_CHARS]


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
    found = _find_headings(lines)
    all_caps = len(_strict_headings(lines)) >= 2 or not found
    # (list index in `items`, line index where this item's heading sits)
    headings: list[tuple[int, int]] = []
    items: list[AgendaItem] = []
    for line_no, number, raw in found:
        # All-caps headings are title-cased for readability; a mixed-case heading
        # is already readable and must not be re-cased.
        title = _title_case(raw) if raw.isupper() or all_caps else raw
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
