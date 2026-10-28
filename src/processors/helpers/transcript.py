"""Parse a transcript into timestamped utterances (TRA-176, increment 2).

Transcripts are written as repeating blocks::

    [Speaker A] (0:00:19 - 0:00:26)
    All right, it is 7 PM and I will call this meeting to order...

The section engine needs these as structured ``(speaker, start, end, text)`` records
so it can slice the transcript into per-agenda-item spans by timestamp. Pure and
unit-testable; no model or I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "[Speaker A] (0:00:19 - 0:00:26)" — hours:minutes:seconds, hours 1+ digits. Many
# transcripts instead print minutes:seconds ("(0:53 - 1:05)") until the hour mark and
# switch to hours:minutes:seconds after it, so a header may use either form.
_TS = r"\d+(?::\d{2}){1,2}"
_HEADER_RE = re.compile(
    rf"^\[(?P<speaker>[^\]]+)\]\s*\((?P<start>{_TS})\s*-\s*(?P<end>{_TS})\)\s*$"
)


def hms_to_seconds(hms: str) -> int:
    """``"1:02:03"`` -> ``3723`` seconds; also accepts ``"M:SS"`` (``"1:03"`` -> 63)."""
    parts = [int(p) for p in hms.split(":")]
    if len(parts) == 2:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def seconds_to_hms(seconds: int) -> str:
    """``3723`` -> ``"1:02:03"`` (matches the transcript's own timestamp form)."""
    h, rem = divmod(max(0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


@dataclass(frozen=True)
class Utterance:
    speaker: str
    start: int  # seconds from meeting start
    end: int  # seconds from meeting start
    text: str


def parse_utterances(transcript: str) -> list[Utterance]:
    """Parse a transcript into ordered utterances.

    Lines before the first header (metadata, a title) are ignored. An utterance's
    body is every line up to the next header, joined and stripped.
    """
    utterances: list[Utterance] = []
    header: tuple[str, int, int] | None = None  # (speaker, start, end)
    body: list[str] = []

    def flush() -> None:
        if header is not None:
            speaker, start, end = header
            utterances.append(Utterance(speaker, start, end, "\n".join(body).strip()))

    # A leading BOM (transcripts are written utf-8-sig style) is not stripped by
    # str.strip() and would defeat the first header, dropping the opening utterance.
    for line in transcript.lstrip("\ufeff").splitlines():
        m = _HEADER_RE.match(line.strip())
        if m:
            flush()
            header = (
                m.group("speaker"),
                hms_to_seconds(m.group("start")),
                hms_to_seconds(m.group("end")),
            )
            body = []
        elif header is not None:
            body.append(line)
    flush()
    return utterances


def utterances_in_span(
    utterances: list[Utterance], start: int, end: int | None
) -> list[Utterance]:
    """Utterances whose start falls in ``[start, end)`` (``end=None`` -> to the end)."""
    return [
        u for u in utterances if u.start >= start and (end is None or u.start < end)
    ]


def render_span(utterances: list[Utterance]) -> str:
    """Render utterances back to the ``[Speaker] (h:mm:ss)\\ntext`` block form, for
    feeding one agenda item's span to the summarizer."""
    return "\n\n".join(
        f"[{u.speaker}] ({seconds_to_hms(u.start)})\n{u.text}" for u in utterances
    )
