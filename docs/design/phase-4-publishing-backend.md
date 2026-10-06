# Phase 4 publishing backend: MediaWiki vs. microfeed vs. MkDocs

Status: **Decided — MkDocs (static, content-repo per city, Cloudflare).** Design discussion, 2026-10.

## Context

The pipeline scrapes a city's meetings and publishes to YouTube (video), Google
Drive (transcripts/docs), and a **wiki** (per-meeting + per-year pages linking the
assets). Today the wiki is MediaWiki, written via `pywikibot` in
`src/processors/update_wiki.py`.

**Phase 4 generalizes the pipeline to any CA city.** One MediaWiki instance per
city is heavy: each is a server + DB to host, patch, spam-guard, and upgrade. We
want a lighter per-city publishing target. Phase 3 already makes disk the
**canonical** store and publishing **pluggable/optional** behind the `Publisher`
abstraction (`src/publishers/`, grouped by `destination`), so the wiki is just one
swappable target — the source of truth stays on disk/in the pipeline.

## Options considered

### 1. MediaWiki (today)
- **For:** real wiki — arbitrary hyperlinked pages, categories, full-text search,
  revision history, in-browser collaborative editing. Battle-tested, portable.
- **Against:** per-city server + DB + ops (patching, spam, upgrades). Scales
  poorly to many cities. Overkill for a pipeline-authored, read-mostly site.

### 2. microfeed on Cloudflare (D1/R2/Workers CMS)
- **For:** serverless, cheap on the existing Workers paid plan; a meeting maps
  cleanly onto a feed *item*; built-in RSS/JSON feeds + reader; scales per-city.
- **Against:** it's a **feed/CMS, not a wiki** — no arbitrary pages, no
  hierarchical cross-linked navigation, weak search, no page history. Smaller OSS
  project with a Cloudflare-specific, less-proven write API. Ruled out because it
  drops the wiki-style structure we want to keep.

### 3. MkDocs (static site generator) — **chosen**
- **For:**
  - **Python-native** → low integration friction; reuses the page-rendering logic
    already in `update_wiki.py` (emit Markdown instead of calling `pywikibot`).
  - **Wiki-style structure restored:** hierarchical nav (year → meeting pages),
    static pages (About/How-to), Markdown cross-links with **build-time broken-link
    validation**; `mkdocs-material` ships strong client-side search.
  - **Deterministic render of canonical data** → a static site is a pure function
    of pipeline output, so idempotency is trivial (regenerate) and most of the
    wiki-destination stale-link auditing in `reconcile` goes away.
  - **Git is the audit trail** — generated Markdown lives in a repo, so
    history/diffs/blame replace MediaWiki page history (better provenance for a
    transparency project).
  - Cheap static hosting on Cloudflare; scales per-city like microfeed but **with**
    wiki structure. Can still emit RSS via `mkdocs-rss-plugin`.
- **Against:** no in-browser editing (edit Markdown + rebuild); a build/deploy
  step instead of instant API writes (fine for a scheduled batch pipeline); build
  time / search-index size grows with content (mitigated below).

## Decisions (resolved)

1. **Content flow: push Markdown to a per-city content repo.** The pipeline
   generates Markdown and pushes it to a **separate content repository per city**.
   A commit to that repo triggers the Cloudflare build/deploy. This also solves the
   single-authorship concern: each city's content repo can be opened to **trusted
   maintainers** who edit via normal git PRs — collaborative editing without a
   server or a CMS, and without in-browser editing (which we don't need yet).
2. **Hosting: Cloudflare — cheapest/easiest wins.** Lean toward **Cloudflare
   Pages** (git-connected, builds on commit, effectively free). **Workers Static
   Assets** is the alternative if we later want dynamic routes on the same origin
   (e.g. an `/ask` endpoint proxying the Phase 3 RAG server, redirects, feeds) —
   static docs + a thin Worker is a clean hybrid we can adopt without migrating.
3. **Search: over AI summaries / LLM-generated keywords, not full transcripts.**
   The wiki has no full-text transcript search today, so we don't need to index
   full transcripts (which would bloat the client search index). Index the
   per-meeting **AI summaries** and/or **LLM-generated keywords**; deep transcript
   Q&A stays with the **Phase 3 RAG server**. The same generated Markdown is also
   trivially ingestible by RAG, so docs and retrieval share one content source.
4. **No in-browser editing at this stage.** Git-based edits by maintainers suffice;
   revisit a git-backed CMS overlay (e.g. Decap) only if a city needs it.
5. **MkDocs is the Phase 4 wiki backend.** Standardize on it; keep MediaWiki as a
   still-pluggable target during transition.

## Integration sketch

- New **`MkDocsPublisher`** (a `docs` destination in `src/publishers/`): render
  per-meeting and per-year Markdown from the same meeting data `update_wiki.py`
  uses, write into the per-city content tree, and push to that city's content repo
  (Cloudflare builds/deploys on commit).
- **`reconcile`**: the wiki destination becomes "is the site a current render of
  the data" — mostly "rebuild" rather than stale-link auditing.
- **Per-city config**: a templated `mkdocs.yml` + `mkdocs-material` theme per city,
  provisioned alongside the content repo.

## Open items to validate in a prototype

- Build time and search-index size on real data (confirm summaries/keywords keep
  the index small).
- Content-repo provisioning + the push/deploy mechanics (pipeline → content repo →
  Cloudflare build).
- Exact summary/keyword generation step feeding the search index.

## Recommended next step

Prototype `MkDocsPublisher` for **Clayton only**: generate Markdown from existing
meeting data, `mkdocs-material`, push to a Clayton content repo, deploy on
Cloudflare; A/B against MediaWiki behind the publisher flag; judge build time and
search UX; then standardize for Phase 4.
