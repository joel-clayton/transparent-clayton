"""The RAG vector store — a single sqlite-vec file (TRA-176, increment 2).

One file at ``RAG_DB_PATH`` holding section metadata and their embeddings together.
It is a **derived** index: the canonical artifact is each meeting's ``sections.json``
on disk, so the whole store can be rebuilt from disk (and must be, on an embedding-
model change — vectors never mix across models). Never committed (see .gitignore).

Two tables: ``sections`` (one row per agenda-item section, with provenance) and a
``vec_sections`` ``vec0`` virtual table holding the embedding, joined by rowid.
Writes for a meeting are replace-all so re-indexing is idempotent.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

import sqlite_vec

from src.settings import RAG_DB_PATH

_SECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS sections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_type  TEXT NOT NULL,
    meeting_key   TEXT NOT NULL,
    item_index    INTEGER NOT NULL,
    agenda_item   TEXT NOT NULL,
    t_start       INTEGER,
    t_end         INTEGER,
    procedural    INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL,
    keywords      TEXT NOT NULL DEFAULT '[]',
    transcript_ref TEXT,
    video_url     TEXT,
    agenda_url    TEXT,
    packet_url    TEXT,
    segmenter_model TEXT,
    summary_model TEXT,
    summary_model_digest TEXT,
    embed_model   TEXT,
    embed_model_digest TEXT,
    prompt_version TEXT,
    seed          INTEGER,
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_sections_meeting
    ON sections (meeting_type, meeting_key);
"""


@dataclass
class SectionRow:
    """One agenda-item section as stored. ``embedding`` is held separately in the
    vec table; the rest are columns on ``sections``."""

    meeting_type: str
    meeting_key: str
    item_index: int
    agenda_item: str
    summary: str
    t_start: int | None = None
    t_end: int | None = None
    procedural: bool = False
    keywords: list[str] = field(default_factory=list)
    transcript_ref: str | None = None
    video_url: str | None = None
    agenda_url: str | None = None
    packet_url: str | None = None
    segmenter_model: str | None = None
    summary_model: str | None = None
    summary_model_digest: str | None = None
    embed_model: str | None = None
    embed_model_digest: str | None = None
    prompt_version: str | None = None
    seed: int | None = None
    created_at: str | None = None
    embedding: list[float] | None = None


def connect(path: str = RAG_DB_PATH) -> sqlite3.Connection:
    """Open the store, creating the parent dir and loading the sqlite-vec extension.

    Raises if the interpreter's sqlite3 lacks loadable-extension support, with a
    message pointing at the fix (the pyenv build flag) rather than a cryptic
    AttributeError deep in a write.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    if not hasattr(conn, "enable_load_extension"):
        conn.close()
        raise RuntimeError(
            "This Python's sqlite3 lacks loadable-extension support, so sqlite-vec "
            "cannot load. Rebuild the interpreter with "
            "--enable-loadable-sqlite-extensions."
        )
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection, dim: int) -> None:
    """Create the tables if absent. ``dim`` sizes the embedding column (nomic=768);
    it must match every vector written (sqlite-vec enforces it)."""
    conn.executescript(_SECTIONS_DDL)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_sections USING vec0("
        f"embedding float[{dim}])"
    )
    conn.commit()


def _vec_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vec_sections'"
        ).fetchone()
        is not None
    )


def _delete_meeting(conn: sqlite3.Connection, meeting_type: str, key: str) -> None:
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM sections WHERE meeting_type=? AND meeting_key=?",
            (meeting_type, key),
        )
    ]
    if ids:
        marks = ",".join("?" * len(ids))
        # The vec table may not exist yet if no meeting indexed so far had an
        # embedded section (only procedural/not-reached rows); guard the delete.
        if _vec_table_exists(conn):
            conn.execute(f"DELETE FROM vec_sections WHERE rowid IN ({marks})", ids)
        conn.execute(f"DELETE FROM sections WHERE id IN ({marks})", ids)


def replace_meeting_sections(
    conn: sqlite3.Connection, meeting_type: str, key: str, rows: list[SectionRow]
) -> None:
    """Replace all stored sections for one meeting (idempotent re-index).

    Rows without an ``embedding`` are stored in ``sections`` only (e.g. procedural
    rows we choose not to embed) — they carry no vec entry and never match a search.
    """
    embedded = [r for r in rows if r.embedding]
    if embedded:
        ensure_schema(conn, len(embedded[0].embedding or []))
    else:
        conn.executescript(_SECTIONS_DDL)
    _delete_meeting(conn, meeting_type, key)
    for r in rows:
        cur = conn.execute(
            """INSERT INTO sections (
                meeting_type, meeting_key, item_index, agenda_item, t_start, t_end,
                procedural, summary, keywords, transcript_ref, video_url, agenda_url,
                packet_url, segmenter_model, summary_model, summary_model_digest,
                embed_model, embed_model_digest, prompt_version, seed, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.meeting_type,
                r.meeting_key,
                r.item_index,
                r.agenda_item,
                r.t_start,
                r.t_end,
                int(r.procedural),
                r.summary,
                json.dumps(r.keywords),
                r.transcript_ref,
                r.video_url,
                r.agenda_url,
                r.packet_url,
                r.segmenter_model,
                r.summary_model,
                r.summary_model_digest,
                r.embed_model,
                r.embed_model_digest,
                r.prompt_version,
                r.seed,
                r.created_at,
            ),
        )
        if r.embedding:
            conn.execute(
                "INSERT INTO vec_sections (rowid, embedding) VALUES (?, ?)",
                (cur.lastrowid, sqlite_vec.serialize_float32(r.embedding)),
            )
    conn.commit()


def search(
    conn: sqlite3.Connection,
    embedding: list[float],
    *,
    k: int = 5,
    meeting_type: str | None = None,
) -> list[dict[str, Any]]:
    """K nearest section summaries to a query embedding, nearest first.

    Each result is the ``sections`` row as a dict plus a ``distance``. Optionally
    restricted to one meeting type — the type filter is applied over the full ranked
    set (the k-NN over-fetches every vector, then filters and truncates to ``k``), so
    a type-scoped query still returns up to ``k`` results even when the globally
    nearest rows are other types. (At this corpus size over-fetching all vectors is
    cheap; a larger store would move the filter into a vec0 partition column.)
    """
    # The vec table only exists once a meeting with an embedded section is stored;
    # an early/sparse store (only procedural/not-reached sections) has none, so a
    # query returns no matches rather than raising on a missing table.
    if not _vec_table_exists(conn):
        return []
    fetch_k = k
    if meeting_type is not None:
        total = conn.execute("SELECT count(*) FROM vec_sections").fetchone()[0]
        fetch_k = max(k, int(total))
    rows = conn.execute(
        """SELECT s.*, v.distance FROM vec_sections v
           JOIN sections s ON s.id = v.rowid
           WHERE v.embedding MATCH ? AND k = ?
           ORDER BY v.distance""",
        (sqlite_vec.serialize_float32(embedding), fetch_k),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        if meeting_type and d.get("meeting_type") != meeting_type:
            continue
        d["keywords"] = json.loads(d.get("keywords") or "[]")
        out.append(d)
        if len(out) >= k:
            break
    return out


def section_row_to_json(row: SectionRow) -> dict[str, Any]:
    """A SectionRow as a plain dict for ``sections.json`` (embedding dropped — the
    canonical file stays small; vectors are rebuilt from it)."""
    d = asdict(row)
    d.pop("embedding", None)
    return d
