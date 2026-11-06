"""Reset a meeting's AI summary so the summaries stage regenerates it.

Use it when a summary was generated from bad input (e.g. before a parser fix) and
should be redone. For each meeting it removes, together:

  * the summary files on disk (the ``.md`` and its ``.sections.json``),
  * the meeting's rows in the RAG index (sqlite-vec), and
  * its entry in the ``summarized.<type>`` Redis ledger.

The disk files are what make ``--stage summaries`` treat the meeting as missing; the
index rows and ledger entry are cleaned up so nothing stale stays searchable. A
meeting whose summary is a legacy ``.md``/``.txt`` with no ``.sections.json`` (made
by an older generator) is left alone. The wiki is not touched: regenerating the
summary re-flags the meeting and the next wiki pass re-renders it.

Read-only audit by default; ``--execute`` applies. Stop any run that is writing the
same meeting first.

    python scripts/reset_summary.py --type bac_mtg "2024-07-10 04_00 PM"
    python scripts/reset_summary.py --type bac_mtg "2024-07-10 04_00 PM" --execute
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from src.constants import SUMMARIZED_KEY
from src.meeting_types import MEETING_TYPES, MeetingType


@dataclass
class ResetPlan:
    key: str
    files: list[str] = field(default_factory=list)  # engine files to delete
    rag_rows: int = 0
    in_ledger: bool = False
    legacy_only: bool = False  # a .md exists but no .sections.json: not ours

    @property
    def nothing_to_do(self) -> bool:
        return not (self.files or self.rag_rows or self.in_ledger)


def plan_reset(
    key: str,
    md_path: str,
    sections_path: str,
    conn: sqlite3.Connection,
    meeting_type_name: str,
    redis_client: Any,
    ledger_key: str,
) -> ResetPlan:
    """What would be removed for one meeting. Pure of side effects."""
    plan = ResetPlan(key=key)
    if os.path.exists(sections_path):
        plan.files = [p for p in (md_path, sections_path) if os.path.exists(p)]
    elif os.path.exists(md_path):
        plan.legacy_only = True
    plan.rag_rows = conn.execute(
        "SELECT COUNT(*) FROM sections WHERE meeting_type=? AND meeting_key=?",
        (meeting_type_name, key),
    ).fetchone()[0]
    plan.in_ledger = bool(redis_client.hexists(ledger_key, key))
    return plan


def apply_reset(
    plan: ResetPlan,
    conn: sqlite3.Connection,
    meeting_type_name: str,
    redis_client: Any,
    ledger_key: str,
    delete_rows: Any,
) -> None:
    """Remove the files, index rows and ledger entry. ``delete_rows`` is
    ``rag_store._delete_meeting`` (injected so tests need not load sqlite-vec)."""
    for path in plan.files:
        os.remove(path)
    delete_rows(conn, meeting_type_name, plan.key)
    conn.commit()
    redis_client.hdel(ledger_key, plan.key)


def _meeting_type(type_key: str) -> MeetingType:
    for mt in MEETING_TYPES:
        if mt.key == type_key:
            return mt
    valid = ", ".join(mt.key for mt in MEETING_TYPES)
    raise SystemExit(f"Unknown --type {type_key!r}; choose one of: {valid}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--type", dest="type_key", required=True, help="e.g. bac_mtg")
    parser.add_argument(
        "meeting_keys", nargs="+", help='meeting key(s), e.g. "2024-07-10 04_00 PM"'
    )
    parser.add_argument(
        "--execute", action="store_true", help="apply (default: read-only audit)"
    )
    args = parser.parse_args()

    # Imported here so --help works without Redis/sqlite-vec available.
    from celery_app import r
    from src.processors.helpers import rag_store
    from src.processors.rag_index import RagIndexer
    from src.types import MEETING_TYPE_BY_SOURCE

    meeting_type = _meeting_type(args.type_key)
    source_type = next(
        s for s, mt in MEETING_TYPE_BY_SOURCE.items() if mt is meeting_type
    )
    indexer = RagIndexer(source_type)
    ledger_key = meeting_type.redis_key(SUMMARIZED_KEY)
    conn = rag_store.connect()

    mode = "EXECUTE (writes)" if args.execute else "AUDIT (read-only)"
    print(f"Reset summaries — mode: {mode}  type: {meeting_type.display_name}")
    for key in args.meeting_keys:
        md_path = indexer.construct_filepath_for_date(key)
        plan = plan_reset(
            key,
            md_path,
            indexer._sections_json_path(key),
            conn,
            meeting_type.display_name,
            r,
            ledger_key,
        )
        if plan.legacy_only:
            print(f"  {key}: legacy summary (no sections.json) — left untouched")
            continue
        if plan.nothing_to_do:
            print(f"  {key}: nothing to reset")
            continue
        print(
            f"  {key}: {len(plan.files)} file(s), {plan.rag_rows} index row(s), "
            f"ledger entry: {'yes' if plan.in_ledger else 'no'}"
        )
        for path in plan.files:
            print(f"    delete {path}")
        if args.execute:
            apply_reset(
                plan,
                conn,
                meeting_type.display_name,
                r,
                ledger_key,
                rag_store._delete_meeting,
            )
    if not args.execute:
        print("Audit only — re-run with --execute to apply.")


if __name__ == "__main__":
    main()
