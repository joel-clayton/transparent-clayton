import os
from datetime import datetime


# --- optional .env ----------------------------------------------------------
# There is no hard dependency on python-dotenv. If a `.env` file sits at the
# repo root, load simple KEY=VALUE lines from it into the environment WITHOUT
# overriding anything already set (real env vars and start.sh's exported vars
# win). Lines that are blank, comments, or lack an "=" are ignored; surrounding
# quotes and an optional leading "export " are stripped. This is deliberately
# minimal — for anything fancier, set real environment variables.
def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ").strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


# --- pipeline boundary -------------------------------------------------------
# The single date dividing the live pipeline from the history backfill. The hourly
# scrape and every processor's default floor handle meetings ON OR AFTER it; the
# backfill (scripts/backfill_history.py) handles everything BEFORE it, so no meeting
# is claimed by both or by neither. Move it earlier to hand more history to the live
# pipeline (it will download/upload/transcribe those meetings on its own, spending
# YouTube quota and transcription credits); move it later to backfill more. Set
# PIPELINE_START (ISO date, e.g. 2025-06-01) to override.
PIPELINE_START = datetime.fromisoformat(os.environ.get("PIPELINE_START", "2025-06-01"))


# --- on-disk asset store -----------------------------------------------------
# Root of the on-disk asset store. Overridable (e.g. for a dry run against a
# throwaway directory); defaults to the external volume used in production.
STORAGE_ROOT = os.environ.get("PIPELINE_STORAGE_ROOT", "/Volumes/Gautam/Clayton")

DOWNLOADED_DIR = os.path.join(STORAGE_ROOT, "Downloaded") + os.sep
COMPRESSED_DIR = os.path.join(STORAGE_ROOT, "Compressed") + os.sep
EXTRACTED_AUDIO_DIR = os.path.join(STORAGE_ROOT, "Audio") + os.sep
TRANSCRIBED_DIR = os.path.join(STORAGE_ROOT, "Transcripts") + os.sep
# LLM-generated meeting summaries (per-agenda-section synthesis of transcript +
# agenda + packet), one file per meeting (TRA-176).
SUMMARIES_DIR = os.path.join(STORAGE_ROOT, "Summaries") + os.sep
# Meeting documents (agenda packets, minutes, staff reports) saved to disk so the
# on-disk store is canonical — independent of whether they are also published to
# Google Drive. One folder per meeting under here.
DOCUMENTS_DIR = os.path.join(STORAGE_ROOT, "Documents") + os.sep

# Local Ollama server + the model built from Modelfile-Summarizer, for LLM meeting
# summaries (TRA-176). Env-overridable so another deployment can point elsewhere.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
SUMMARIZER_MODEL = os.environ.get("SUMMARIZER_MODEL", "meeting-summarizer")
# Embedding model for the RAG index (TRA-176 increment 2); swappable so the store
# can be rebuilt with a different model (never mix versions in one index).
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
# Section engine models (TRA-176 increment 2). Segmentation and keyword extraction
# are cheap classification -> a fast model; per-section prose uses the larger model
# (the 14B that won the summary bake-off). All env-overridable.
SEGMENTER_MODEL = os.environ.get("SEGMENTER_MODEL", "qwen3:8b")
SECTION_SUMMARY_MODEL = os.environ.get("SECTION_SUMMARY_MODEL", "qwen3:14b")
KEYWORD_MODEL = os.environ.get("KEYWORD_MODEL", "qwen3:8b")
# A version string for our prompt-assembly templates, recorded in RAG provenance so
# a regeneration can tell whether the prompts changed (bump when they do).
PROMPT_VERSION = "2026-10-20.1"
# The RAG vector store: a single sqlite-vec file under the (off-repo) storage root.
# Derived/rebuildable from each meeting's sections.json; never committed.
RAG_DB_PATH = os.path.join(STORAGE_ROOT, "Rag", "index.db")


# --- Redis -------------------------------------------------------------------
# Host/port were hardcoded to localhost:6379 in celery_app; only the state DB
# (REDIS_DB) was configurable. All are env-overridable now so the broker,
# result backend, and pipeline-state client can point at another host.
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_BROKER_DB = os.environ.get("REDIS_BROKER_DB", "0")
REDIS_BACKEND_DB = os.environ.get("REDIS_BACKEND_DB", "1")
# The pipeline-state client's DB keeps its long-standing REDIS_DB env name.
REDIS_STATE_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_BROKER_DB}"
REDIS_BACKEND_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_BACKEND_DB}"


# --- Google OAuth client secrets --------------------------------------------
# Per-machine paths to the downloaded OAuth client-secret JSON files. These were
# hardcoded to /Users/gautam/... and were not overridable, blocking any
# other-machine install. The cached token files still default to living beside
# each secret (YOUTUBE_TOKEN_FILE / DRIVE_TOKEN_FILE keep their own overrides).
YOUTUBE_CLIENT_SECRET_FILE = os.environ.get(
    "YOUTUBE_CLIENT_SECRET_FILE", "/Users/gautam/dev/client_secret.json"
)
DRIVE_CLIENT_SECRET_FILE = os.environ.get(
    "DRIVE_CLIENT_SECRET_FILE",
    "/Users/gautam/dev/client_secret_721413148557-p0c4gqeha85bo7astbjc9c29hp3a30b6"
    ".apps.googleusercontent.com.json",
)


# --- transcript sharing ------------------------------------------------------
# Comma-separated list of emails each uploaded transcript is shared with. Was a
# hardcoded personal address; override with TRANSCRIPT_SHARE_LIST (empty ->
# share with no one).
TRANSCRIPT_SHARE_LIST = [
    email.strip()
    for email in os.environ.get(
        "TRANSCRIPT_SHARE_LIST", "grahamjordan2596@gmail.com"
    ).split(",")
    if email.strip()
]


# --- publishing destinations -------------------------------------------------
# Output destinations to turn OFF, comma-separated; empty means every destination
# is enabled (today's behavior). Valid names are the publisher destinations:
# "youtube", "google_docs", "wiki". The special token "all" turns every remote
# destination off for a disk-only install (assets are still produced on disk).
# Examples:
#   DISABLED_PUBLISHERS=""              -> publish to all (default)
#   DISABLED_PUBLISHERS="wiki"          -> everything except the wiki
#   DISABLED_PUBLISHERS="youtube,wiki"  -> only Google Docs
#   DISABLED_PUBLISHERS="all"           -> disk-only, no remote publishing
# The pipeline builds its workflow chain from the enabled destinations and the
# registry (src.publishers) consults this for each stage.
DISABLED_PUBLISHERS = {
    name.strip()
    for name in os.environ.get("DISABLED_PUBLISHERS", "").split(",")
    if name.strip()
}


# --- Google Drive parent folders --------------------------------------------
# Drive folder ids. SOURCE_MATERIAL_PARENT_ID is the shared parent that holds
# each type's transcript/document folders; CC_MTG_PARENT_FOLDER_ID is a legacy
# per-type id kept for compatibility. Both are env-overridable for a different
# Drive.
SOURCE_MATERIAL_PARENT_ID = os.environ.get(
    "SOURCE_MATERIAL_PARENT_ID", "177gAQr7VqjlKNoqH-TbBkkQfoXG9yLGn"
)
CC_MTG_PARENT_FOLDER_ID = os.environ.get(
    "CC_MTG_PARENT_FOLDER_ID", "1MR8u-c-eFDXSPef1tHFFknivWjJ5tp79"
)
