"""One-time migration: seed the ``enriched.<type>`` ledger from existing summaries.

The enrich stage (:class:`~src.processors.enrich.Enricher`) took over the wiki-
refresh trigger from the summary stage. Its "already done" ledger (ENRICHED_KEY)
starts empty, so on its first run it would treat *every* meeting that already has a
summary on disk as needing enrichment and flag them all for wiki re-render — a mass,
pointless republish of pages that are already correct.

This script records the current summaries as already enriched, so going forward only
a genuinely new or (re)generated summary triggers enrichment + a wiki refresh — the
same churn-free behaviour the summary stage had before the decouple. Run it once,
with the worker stopped, right after deploying the enrich stage. It is idempotent
(a second run adds nothing) and never flags the wiki.

To re-enrich a specific meeting later (e.g. to publish its new section deep links),
drop it from the ledger: ``HDEL enriched.<type> "<meeting key>"`` — the next enrich
run then re-processes just that one.

Read-only audit by default; ``--execute`` applies.

    python scripts/seed_enriched_ledger.py                 # audit all types
    python scripts/seed_enriched_ledger.py --execute
    python scripts/seed_enriched_ledger.py --type cc_mtg --execute
"""

from __future__ import annotations

import argparse

from src.constants import ENRICHED_KEY
from src.meeting_types import MEETING_TYPES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--type", dest="type_key", help="one type key (e.g. cc_mtg); default: all"
    )
    parser.add_argument(
        "--execute", action="store_true", help="apply (default: read-only audit)"
    )
    args = parser.parse_args()

    # Imported here so --help works without Redis available.
    from celery_app import r
    from src.processors.rag_index import scan_summaries

    types = [mt for mt in MEETING_TYPES if not args.type_key or mt.key == args.type_key]
    if args.type_key and not types:
        valid = ", ".join(mt.key for mt in MEETING_TYPES)
        raise SystemExit(f"Unknown --type {args.type_key!r}; choose one of: {valid}")

    mode = "EXECUTE (writes)" if args.execute else "AUDIT (read-only)"
    print(f"Seed enriched ledger — mode: {mode}")
    for mt in types:
        indexed, legacy = scan_summaries(mt)
        summarized = sorted(indexed | legacy)
        ledger_key = mt.redis_key(ENRICHED_KEY)
        already = {
            k.decode("utf-8") if isinstance(k, bytes) else k
            for k in (r.hkeys(ledger_key) or [])
        }
        to_add = [k for k in summarized if k not in already]
        print(
            f"  {mt.display_name}: {len(summarized)} summarized, "
            f"{len(already)} already in ledger, {len(to_add)} to seed"
        )
        if args.execute and to_add:
            r.hset(ledger_key, mapping={k: 1 for k in to_add})
    if not args.execute:
        print("Audit only — re-run with --execute to apply.")


if __name__ == "__main__":
    main()
