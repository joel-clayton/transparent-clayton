import os

# Root of the on-disk asset store. Overridable (e.g. for a dry run against a
# throwaway directory); defaults to the external volume used in production.
STORAGE_ROOT = os.environ.get(
    "PIPELINE_STORAGE_ROOT", "/Volumes/Gautam/Clayton/CC Meetings"
)

DOWNLOADED_DIR = os.path.join(STORAGE_ROOT, "Downloaded") + os.sep
COMPRESSED_DIR = os.path.join(STORAGE_ROOT, "Compressed") + os.sep
EXTRACTED_AUDIO_DIR = os.path.join(STORAGE_ROOT, "Audio") + os.sep
TRANSCRIBED_DIR = os.path.join(STORAGE_ROOT, "Transcripts") + os.sep
