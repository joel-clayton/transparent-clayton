"""Per-agenda-item summary + keywords for the section engine (TRA-176, increment 2).

Each reached, non-procedural item is summarized from *only* its own transcript span
and agenda text — small inputs, so no context overflow, and the prompt forbids the
failure modes the whole-meeting summarizer showed (inventing quotes/evidence for
items not discussed, procedural padding, trailing meta-commentary). Procedural items
get a terse factual line, not prose; items never taken up get a fixed note. Keywords
are a cheap second pass over the summary (the ticket's "keywords, source: summaries").
"""

from __future__ import annotations

import json
import logging

from src.processors.helpers.agenda_items import AgendaItem
from src.processors.helpers.ollama_client import generate
from src.processors.helpers.transcript import seconds_to_hms

logger = logging.getLogger(__name__)

NOT_TAKEN_UP = "This item was not taken up at the meeting."

# Deterministic-ish: a fixed seed with low temperature. Prose keeps a little
# temperature for readability; classification is pinned to 0.
_SUMMARY_SEED = 42
_SUMMARY_TEMPERATURE = 0.3

_SECTION_SYSTEM = (
    "You are summarizing ONE agenda item of a city council meeting. You are given "
    "the AGENDA TEXT for that item and the TRANSCRIPT SPAN where it was discussed.\n"
    "Rules:\n"
    "- Use ONLY the provided text. Never invent names, quotes, votes, figures, or "
    "events. Do not reuse a quote or fact from a different item.\n"
    "- Use the AGENDA TEXT to state what the item concerns (sub-item titles, "
    "resolution numbers, dollar amounts, named parties); use the TRANSCRIPT SPAN "
    "for what was said and decided.\n"
    "- Report what actually happened: positions taken, speakers (by name only if "
    "the text names them), motions, votes and their outcomes, and any amendments.\n"
    "- Cite key timestamps in [h:mm:ss] for motions, votes, and decisions.\n"
    "- Neutral, factual, concise: 80-250 words. No preamble, no restating the task, "
    "no meta-commentary, and no closing summary sentence. Begin with the content.\n"
    "- If the span contains no substantive discussion of this item, say that in one "
    "sentence and stop."
)

_KEYWORD_SYSTEM = (
    "Extract 4 to 8 short topical keywords or phrases (1-4 words each) that capture "
    "the substance of this city council agenda-item summary: proper nouns, policies, "
    "projects, decisions, dollar amounts. Exclude generic words like 'meeting', "
    "'council', 'agenda', 'item'. Output only JSON."
)

_KEYWORD_SCHEMA = {
    "type": "object",
    "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["keywords"],
}


def summarize_section(item: AgendaItem, span_text: str, *, model: str) -> str:
    """Prose summary of one reached, non-procedural agenda item."""
    prompt = (
        f"AGENDA ITEM {item.number}: {item.title}\n"
        f"{item.body or '(no agenda detail)'}\n\n"
        f"TRANSCRIPT SPAN:\n{span_text}"
    )
    out = generate(
        model,
        prompt,
        system=_SECTION_SYSTEM,
        think=False,
        options={"seed": _SUMMARY_SEED, "temperature": _SUMMARY_TEMPERATURE},
    ).strip()
    return out


def procedural_line(item: AgendaItem, t_start: int) -> str:
    """A one-line factual note for a procedural item (no LLM, no padding)."""
    return f"{item.title} at [{seconds_to_hms(t_start)}]."


def extract_keywords(text: str, *, model: str, max_keywords: int = 8) -> list[str]:
    """Topical keywords for a section summary, via a cheap JSON-constrained pass.

    Returns ``[]`` on empty input or unparseable output (keywords are enrichment,
    never load-bearing).
    """
    if not text.strip():
        return []
    raw = generate(
        model,
        text,
        system=_KEYWORD_SYSTEM,
        think=False,
        format=_KEYWORD_SCHEMA,
        options={"seed": _SUMMARY_SEED, "temperature": 0},
    )
    try:
        keywords = json.loads(raw).get("keywords", [])
    except (ValueError, TypeError):
        logger.warning("Keyword pass returned unparseable JSON")
        return []
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords:
        k = str(kw).strip()
        low = k.lower()
        if k and low not in seen:
            seen.add(low)
            out.append(k)
    return out[:max_keywords]
