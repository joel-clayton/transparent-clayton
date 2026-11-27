"""Deep-link assembly for the enrich stage.

Pure, model-free helpers that turn a meeting's canonical ``sections.json`` into the
publishable wiki ``.md`` with a per-section "watch from here" deep link into the
backup video. Kept separate from the Enricher processor so the link formatting is
unit-testable without Redis or disk.

The links are additive and best-effort: a section with no usable timestamp, or a
meeting with no (single-part) video, simply renders without a link — the summary
prose is never withheld. Markdown link syntax (``[label](url)``) is used because the
wiki already converts it to MediaWiki external-link markup (see
``update_wiki._markdown_to_mediawiki``); the ``.md`` also stays readable on its own.
"""

from __future__ import annotations

from typing import List, Mapping

from src.processors.helpers.section_summary import NOT_TAKEN_UP

# Prefix on the watch link's label; also a human cue in the raw .md.
_WATCH = "▶ Watch from"


def format_hms(seconds: int) -> str:
    """Seconds → compact clock label: ``0:19``, ``41:08``, ``1:02:03`` (hours only
    when present). Used for the link label, not the URL offset."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def youtube_at(video_url: str, t_start: int) -> str:
    """A YouTube watch URL with a start offset. The backup links already carry a
    ``?v=`` query, so the offset is appended with ``&t=<n>s`` (the form YouTube
    honours for a deep link into a single video)."""
    sep = "&" if "?" in video_url else "?"
    return f"{video_url}{sep}t={int(t_start)}s"


def _section_block(section: Mapping, video_url: str | None) -> str:
    """One section's ``.md`` block: the same ``## Item N: Title`` heading the summary
    stage emits, an optional watch-deep-link line, the summary prose, and keywords.
    Mirrors ``RagIndexer._assemble_markdown`` so the wiki parses it identically."""
    number = section["item_index"]
    title = section["agenda_item"]
    summary = section["summary"]
    lines = [f"## Item {number}: {title}", ""]

    t_start = section.get("t_start")
    # No link for an item that was never reached (its timestamp is just where the
    # meeting moved past it — pointing a viewer there is misleading), or when the
    # section has no timestamp or the meeting has no single-part video to link to.
    if video_url and t_start is not None and summary != NOT_TAKEN_UP:
        label = f"{_WATCH} {format_hms(t_start)}"
        lines += [f"[{label}]({youtube_at(video_url, t_start)})", ""]

    lines.append(summary)
    keywords = section.get("keywords") or []
    if keywords:
        lines += ["", f"**Keywords:** {', '.join(keywords)}"]
    return "\n".join(lines)


def assemble_enriched_markdown(sections: List[Mapping], video_url: str | None) -> str:
    """Stitch ``sections.json`` sections into the enriched wiki ``.md``. With no
    ``video_url`` this is byte-identical to the summary stage's base ``.md`` (the
    enrichment is purely additive), so an un-enrichable meeting loses nothing."""
    return "\n\n".join(_section_block(s, video_url) for s in sections) + "\n"
