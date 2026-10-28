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
import re
from collections import Counter

from src.processors.helpers.agenda_items import AgendaItem
from src.processors.helpers.ollama_client import generate
from src.processors.helpers.transcript import seconds_to_hms

logger = logging.getLogger(__name__)

NOT_TAKEN_UP = "This item was not taken up at the meeting."

# Safety net: a raw diarization label the model failed to resolve must never reach
# the wiki. Council members get named via the SPEAKER KEY; a residual label is
# usually a non-roster speaker (staff, public) we can't identify, so it becomes a
# generic reference rather than "Speaker G".
_LABEL_RE = re.compile(r"\bSpeaker [A-Z]\b")

# Deterministic-ish: a fixed seed with low temperature. Prose keeps a little
# temperature for readability; classification is pinned to 0.
_SUMMARY_SEED = 42
_SUMMARY_TEMPERATURE = 0.3
# Context windows set explicitly so behavior never depends on a base model's
# Ollama default (qwen3:8b falls back to a small default that would truncate).
# A single agenda-item span can be long (a lengthy public hearing); the key/keyword
# passes see only short inputs.
_SECTION_NUM_CTX = 32768
_UTIL_NUM_CTX = 8192

_SECTION_SYSTEM = (
    "You are summarizing ONE agenda item of a city council meeting. You are given "
    "MEETING CONTEXT (the member roster and the roll call, which map the "
    "transcript's 'Speaker A/B/C' labels to real people), the AGENDA TEXT for the "
    "item, and the TRANSCRIPT SPAN where it was discussed.\n"
    "Rules:\n"
    "- Use ONLY the provided text. Never invent names, quotes, votes, figures, or "
    "events. Do not reuse a quote or fact from a different item.\n"
    "- Name people using the MEETING CONTEXT. It includes a SPEAKER KEY mapping "
    "labels to people (e.g. 'Speaker C = Jim Diaz, Councilmember') — apply it so you "
    "NEVER write 'Speaker C' in the output. A label absent from the key is a member "
    "of the public: use their name if they state it, otherwise 'a member of the "
    "public' or 'a speaker'. Never emit a 'Speaker X' label.\n"
    "- Do not repeat the agenda item number or title as a heading or first line; "
    "begin directly with the summary content.\n"
    "- Use the AGENDA TEXT to state what the item concerns (sub-item titles, "
    "resolution numbers, dollar amounts, named parties); use the TRANSCRIPT SPAN "
    "for what was said and decided.\n"
    "- Be thorough and complete for substantive items: cover every speaker and each "
    "distinct point, position, motion, vote and outcome, and amendment — do not omit "
    "participants or compress multiple speakers into one line. Cite key timestamps "
    "in [h:mm:ss] for motions, votes, and decisions.\n"
    "- Length should fit the substance: roughly 150-500 words for a substantive "
    "item, shorter only when the item genuinely was brief. Do not pad, do not "
    "restate the task, no meta-commentary, no closing summary sentence. Begin with "
    "the content.\n"
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


def summarize_section(
    item: AgendaItem, span_text: str, *, model: str, context: str = ""
) -> str:
    """Prose summary of one reached, non-procedural agenda item.

    ``context`` is the per-meeting roster + roll call, so the model can resolve the
    transcript's ``Speaker X`` labels to real names consistently across sections.
    """
    prompt = (
        f"MEETING CONTEXT:\n{context or '(none provided)'}\n\n"
        f"AGENDA ITEM {item.number}: {item.title}\n"
        f"{item.body or '(no agenda detail)'}\n\n"
        f"TRANSCRIPT SPAN:\n{span_text}"
    )
    out = generate(
        model,
        prompt,
        system=_SECTION_SYSTEM,
        think=False,
        options={
            "seed": _SUMMARY_SEED,
            "temperature": _SUMMARY_TEMPERATURE,
            "num_ctx": _SECTION_NUM_CTX,
        },
    ).strip()
    return _clean_summary(out, item)


def _clean_summary(text: str, item: AgendaItem) -> str:
    """Strip any residual ``Speaker X`` labels and a leading echoed item heading.

    Only an actual echoed heading is removed — a Markdown heading line, or a first
    line that *equals* a known heading form — so prose that merely begins with the
    item number (e.g. "Item 3 funding was approved 4-1...") is preserved."""
    text = _LABEL_RE.sub("a speaker", text).strip()
    head, _, rest = text.partition("\n")
    stripped = head.strip()
    normalized = stripped.lstrip("#").strip().rstrip(":").lower()
    title = item.title.lower()
    heading_forms = {
        title,
        f"item {item.number}",
        f"item {item.number}: {title}",
        f"item {item.number} {title}",
        f"item {item.number} - {title}",
    }
    if stripped.startswith("#") or normalized in heading_forms:
        text = rest.strip()
    return text


_SPEAKER_KEY_SYSTEM = (
    "You map the anonymous speaker labels in a city meeting's opening to real "
    "people. You are given the MEMBER ROSTER (and, when present, the MINUTES ROLL "
    "CALL: who actually attended, with titles, which is authoritative over the "
    "agenda roster) and the OPENING transcript. Where the clerk reads each member's "
    "name and that member answers 'present'/'here' on the next line, use that. A "
    "committee may instead take a silent visual roll call; then use only strong "
    "cues in the opening, such as a speaker addressed by name or title, or the chair "
    "naming themself. For each 'Speaker X' label, give the person's name and role "
    "(e.g. 'Jim Diaz, Councilmember') ONLY when the evidence makes it clear; omit a "
    "label you cannot confidently identify. Output only JSON."
)

_SPEAKER_KEY_SCHEMA = {
    "type": "object",
    "properties": {
        "speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["label", "name"],
            },
        }
    },
    "required": ["speakers"],
}


def build_speaker_key(roster: str, roll_call: str, *, model: str) -> str:
    """A ``Speaker X = Name, Role`` key derived once per meeting from the roster +
    roll call, to hand to every section so council members are named consistently
    instead of each section re-inferring from roll-call adjacency. Returns "" if the
    mapping can't be built (best-effort; sections still get the roster)."""
    if not roll_call.strip():
        return ""
    prompt = f"MEMBER ROSTER:\n{roster or '(none)'}\n\nROLL CALL:\n{roll_call}"
    raw = generate(
        model,
        prompt,
        system=_SPEAKER_KEY_SYSTEM,
        think=False,
        format=_SPEAKER_KEY_SCHEMA,
        options={"seed": _SUMMARY_SEED, "temperature": 0, "num_ctx": _UTIL_NUM_CTX},
    )
    try:
        speakers = json.loads(raw).get("speakers", [])
    except (ValueError, TypeError):
        logger.warning("Speaker-key pass returned unparseable JSON")
        return ""
    named = [s for s in speakers if s.get("label") and s.get("name")]
    # A small model sometimes pins several labels on one person (observed: two
    # different speakers both mapped to the same staff member). One person rarely
    # holds two labels, so a name given to more than one label is ambiguous: drop it
    # rather than mislabel speakers in every section.
    counts = Counter(s["name"].strip().lower() for s in named)
    lines = [
        f"{s['label']} = {s['name']}"
        for s in named
        if counts[s["name"].strip().lower()] == 1
    ]
    return "\n".join(lines)


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
        options={"seed": _SUMMARY_SEED, "temperature": 0, "num_ctx": _UTIL_NUM_CTX},
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
