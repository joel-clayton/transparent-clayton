import os


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


# --- on-disk asset store -----------------------------------------------------
# Root of the on-disk asset store. Overridable (e.g. for a dry run against a
# throwaway directory); defaults to the external volume used in production.
STORAGE_ROOT = os.environ.get(
    "PIPELINE_STORAGE_ROOT", "/Volumes/Gautam/Clayton/CC Meetings"
)

DOWNLOADED_DIR = os.path.join(STORAGE_ROOT, "Downloaded") + os.sep
COMPRESSED_DIR = os.path.join(STORAGE_ROOT, "Compressed") + os.sep
EXTRACTED_AUDIO_DIR = os.path.join(STORAGE_ROOT, "Audio") + os.sep
TRANSCRIBED_DIR = os.path.join(STORAGE_ROOT, "Transcripts") + os.sep


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
