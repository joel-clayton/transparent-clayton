# Phase 3b — RAG chunking: agenda-section summaries, keywords, embeddings

Status: **Built 2026-10-20** (design discussion 2026-10). Tracks TRA-176. See
"Implementation notes" at the end for how the build differs from this plan.

## Context

Increment 1 (shipped on `tra-176-summarizer`) added a `Summarizer(Processor)` stage
that feeds the whole meeting — transcript + agenda + agenda packet — to the local
Ollama `meeting-summarizer` model in one call, writes one `.md` per meeting to
`Summaries/`, and fills the wiki's AI-summary section. That works for short meetings
but has two limits TRA-176 is really about:

1. **Context overflow.** `num_ctx` is 32k (~50 pages). A real agenda packet is often
   bigger, so a single whole-meeting prompt silently truncates.
2. **No retrieval granularity.** TRA-176 wants "a whole year to fit into context" —
   i.e. timestamped summaries and keyword lists grouped by agenda item, so a later
   RAG/query layer retrieves only the relevant pieces instead of stuffing a year in.

Increment 2 restructures summarization to be **section-driven** and builds the RAG
index.

## Decisions (2026-10)

- **Vector store: `sqlite-vec`** — a single file, SQL + vectors + metadata together,
  no server. Chosen for ease of use and portability (Phase 4 generalizes to other
  cities; a file store travels trivially) over a server DB (pgvector/Chroma).
- **A new stage, `RagIndexer`** — separate from the increment-1 Summarizer rather
  than overloading it. It becomes the authoritative section engine: it supersedes
  the whole-meeting generation (producing the same `Summaries/*.md` for the wiki, now
  *assembled from sections*) **and** emits the RAG artifacts. The increment-1
  whole-meeting path was the scaffolding (stage wiring, wiki seam, legacy handling);
  increment 2 swaps the chain's `summarize` stage for `rag_index`.
- **Keywords: LLM-from-summary** — a second, cheap LLM pass over each section summary
  (the ticket's "keywords, source: summaries"), rather than KeyBERT. Simple and
  consistent; revisit if quality is poor.

## Pipeline (per meeting)

1. **Segment into agenda items.** Parse the Agenda PDF for the item list, then map
   transcript spans (the transcript carries `[Speaker] (H:MM:SS)` timestamps) to each
   item — see "Segmentation" below.
2. **Per item — summarize + keyword.** Feed only that item's transcript span + the
   matching packet excerpt → a short timestamped summary; then a keyword pass over
   that summary. **Procedural items** (roll call, minutes approval, adjournment) are
   detected and get **keywords only**, no prose.
3. **Embed + store.** Embed each section summary → `sqlite-vec`, with metadata.
4. **Assemble.** Stitch the section summaries into the per-meeting `Summaries/*.md`
   (the wiki source) — which now fits context because each section was summarized
   separately.

Retrieval-time, "a year fits in context" is achieved by querying the vector store for
the relevant section summaries, not by prompting with a year of transcripts.

## Segmentation (the main risk)

Transcripts don't cleanly announce agenda boundaries, so this is the hard part and
will be built iteratively:

- **Start: LLM windowed tagging.** Slide the transcript in ~15–20-minute windows;
  give the model the agenda item list and ask which item(s) each window covers; stitch
  into spans. Bounded context, handles any length.
- **Fallbacks:** chair-cue heuristics ("moving to item 3"); embedding-similarity
  alignment between agenda-item text and transcript windows. When the agenda is
  missing/unparseable, fall back to time-windowed chunks with best-effort labels.

Build the segmenter behind a small interface so the strategy can evolve without
touching the rest of the stage; eyeball its output on a few real meetings first.

## Embeddings

- **Swappable model:** `EMBEDDING_MODEL` setting (default `nomic-embed-text` — already
  pulled; its 8192-token context suits section-level chunks), sibling to
  `SUMMARIZER_MODEL`, used via the existing `ollama_client.embed`.
- **Model-aware prefixes, baked in:** all embedding goes through two helpers,
  `embed_document(text)` and `embed_query(text)`, which apply the model's required
  prefixes (`search_document:` / `search_query:` for nomic; the correct pair if swapped
  to bge/e5) from a small per-model table — so stored docs and queries can never drift.
- **What we embed:** the section summary is the primary vector (concise, dense); the
  raw span and links are metadata. Raw spans can be embedded too later if recall needs it.

## Store schema (sqlite-vec)

A file at `<STORAGE_ROOT>/Rag/index.db`. Two linked tables:

- `sections` (metadata, one row per agenda-item section): `id`, `meeting_type`,
  `meeting_key`, `item_index`, `agenda_item`, `t_start`, `t_end`, `procedural`,
  `summary`, `keywords` (JSON), `transcript_ref`, `video_url` (deep-linked to
  `t_start` where possible), `agenda_url`, `packet_url`, plus the **provenance**
  fields below, `created_at`.
- a `vec0` virtual table holding the embedding, joined to `sections` by rowid.

The DB is a **derived index**: the canonical, reconcilable artifact is a per-meeting
`sections.json` on disk (next to the `.md`), so the whole store can be rebuilt from
disk and a city's data stays file-portable. Like every generated artifact it lives
under `STORAGE_ROOT` (off-repo) and is **never committed** — `.gitignore` also
excludes `*.db`/`*.db-wal`/`*.db-shm` defensively in case a dev points `STORAGE_ROOT`
into the repo or a test drops a local db.

## Reproducibility & regeneration

The goal is **"roughly similar" (semantically equivalent), not byte-identical** —
the right bar for an LLM. Each `sections` row records enough provenance to (a)
regenerate comparable output elsewhere and (b) *detect* when a regeneration is even
worth doing (ties into the `legacy_summary` replace-if-better idea):

- `summarizer_model`, `summarizer_digest` — the Ollama **digest**, not just the
  mutable tag (`:latest`/`8b` can be re-pulled to a different blob); from `/api/show`.
- `modelfile_hash` — hash of the model's Modelfile (SYSTEM text **and** `PARAMETER`s
  like `temperature`/`num_ctx` all shape output).
- `prompt_version` — a version string for *our* prompt-assembly template.
- `seed` — a fixed seed passed as Ollama `options.seed`, so a rerun on the same
  machine/model is near-deterministic (temperature 0.3 is otherwise stochastic).
- `embed_model`, `embed_digest` — pinned for the vectors.
- (optional) `pdfplumber_version` / input hashes — document-extraction can vary.

Two honest limits:

- **Cross-machine exact reproduction is not achievable.** Different GPU/CPU,
  quantization, and llama.cpp/Ollama versions give floating-point differences →
  token-level divergence even with a fixed seed. Same model+digest+modelfile+prompt
  +input yields *equivalent, differently-worded* summaries — which is fine here.
- **Never mix embedding-model versions in one index.** Vectors drift slightly across
  machine/quant/version; cosine comparisons only hold within a single
  `embed_model`+`embed_digest`. So "regenerate on another machine" means **rebuild the
  whole vector store** from the canonical `sections.json`, not append to it. A change
  in `embed_digest` is the trigger to rebuild.

## Stage + reconcile integration

- **`RagIndexer`** stage, floor-aware + idempotent (skip a meeting whose `sections.json`
  exists), inserted where `summarize` is today (after docs, before the wiki). It writes
  `sections.json`, the assembled `Summaries/*.md`, and the `sqlite-vec` rows.
- **Reconcile:** replace/extend `MISSING_SUMMARY` with `MISSING_RAG_INDEX` — a
  transcript on disk with no `sections.json`. Optionally cross-check that every
  `sections.json` row is present in the vector DB (the DB is rebuildable, so a drift is
  a rebuild, not data loss).

## Open / later

- **Embedding model bake-off:** nomic for v1; once there's enough corpus, A/B against
  `mxbai-embed-large`/`bge-large` on real queries (and glance at the current MTEB
  retrieval board — models move fast).
- **The query/chat layer** (retrieval + answer synthesis over the store) is the next
  piece after this index exists; out of scope here.
- **Re-indexing legacy summaries:** the `legacy_summary.<type>` set (increment 1)
  marks pre-existing summaries; a later pass can regenerate + quality-compare them.

## Implementation notes (built 2026-10-20)

How the shipped build differs from the plan above, and why:

- **Vector store kept as sqlite-vec, but the interpreter had to be rebuilt.** The
  project's pyenv 3.11.5 was compiled without loadable-extension support, so
  sqlite-vec couldn't load. Rather than swap stores, pyenv 3.11.5 was rebuilt with
  `--enable-loadable-sqlite-extensions` (linked to Homebrew sqlite) and the venv
  recreated. Store schema is as designed (`sections` + `vec0`), one file at
  `RAG_DB_PATH`; only embedded (reached, non-procedural) sections get a vector.
- **Stage reuses the `SUMMARIZE` slot** instead of a new `JobType`/`rag_index`
  stage. `RagIndexer` (src/processors/rag_index.py) is a drop-in replacement for the
  Summarizer at the same chain position — same `Summaries/*.md` output and wiki
  seam — so no new job wiring. Idempotency keys on the per-meeting `sections.json`
  sidecar (``<summary>.sections.json``); legacy summaries (a `.md`/`.txt` with no
  sidecar) are recorded and left alone, as in increment 1.
- **Models:** segmentation + keywords run on a fast model (`qwen3:8b`), per-section
  prose on `qwen3:14b` (the summary bake-off winner). Sections use base models with
  task-specific prompts in code, not a Modelfile — so `modelfile_hash` provenance is
  replaced by `prompt_version` (`PROMPT_VERSION`). All model names are env-settings.
  `think=false` + a fixed `seed` + JSON-schema output make the classification calls
  deterministic and parseable.
- **Segmentation = LLM boundary detection** (windowed, so any transcript length),
  not per-window item tagging: the model reports where each agenda item *begins*,
  boundaries are forced monotonic, and an item with no detected (or zero-length)
  boundary is marked "not taken up" rather than given invented content. Works well
  on real meetings; a known limitation is the fuzzy boundary between adjacent
  report-style items (e.g. council-committee reports landing under "Action Items"
  when no chair cue separates them).
- **Reconcile:** `MISSING_SUMMARY` became `MISSING_RAG_INDEX` (transcript on disk,
  no `sections.json`, and no summary of any kind).
- **Deferred:** populating `video_url`/`agenda_url`/`packet_url` (and video
  deep-links to `t_start`) on section rows — the columns exist but are left null for
  now; and the query/chat layer over the store.
