"""On-disk layout: per-type buckets inside each stage directory (TRA-135).

Each stage directory (``Downloaded/``, ``Compressed/``, ``Audio/``,
``Transcripts/``, ``Documents/``) holds one subdirectory per meeting type, named
by :pyattr:`src.meeting_types.MeetingType.disk_bucket`, mirroring the per-type
Drive folders and YouTube playlists::

    <ROOT>/Compressed/City Council/Clayton CA City Council Meeting ... .mp4

To survive the transition from the old flat layout without an ordering hazard,
reads look in BOTH the per-type bucket and the legacy flat stage dir (see
:func:`read_dirs_for_bucket`), while writes always target the per-type bucket
(see :func:`write_dir_for_bucket`). The legacy fallback is removed in a follow-up
once the one-time migration (``scripts/migrate_disk_layout.py``) has run.
"""

import os
from typing import Iterator


def write_dir_for_bucket(base_dir: str, bucket: str) -> str:
    """The per-type subdirectory ``<base_dir>/<bucket>/`` to write into (always the
    new layout — the pipeline never writes flat). Created only when ``base_dir``
    already exists: the stage dirs live on an external volume, so this must not
    fabricate the stage tree on the boot disk when the volume is unmounted — a
    write then fails loudly against the missing dir, as it did before bucketing."""
    path = os.path.join(base_dir, bucket) + os.sep
    if os.path.isdir(base_dir):
        os.makedirs(path, exist_ok=True)
    return path


def resolve_existing_file(base_dir: str, bucket: str, file_name: str) -> str:
    """Absolute path to ``file_name`` within a stage, preferring the per-type
    bucket and falling back to the legacy flat location. Defaults to the bucket
    path when the file is in neither (so a caller about to write lands in the new
    layout). Does not create anything."""
    bucket_path = os.path.join(base_dir, bucket, file_name)
    if os.path.exists(bucket_path):
        return bucket_path
    flat_path = os.path.join(base_dir, file_name)
    if os.path.exists(flat_path):
        return flat_path
    return bucket_path


def read_dirs_for_bucket(base_dir: str, bucket: str) -> list[str]:
    """Directories to read a stage's files from, most-current first: the per-type
    bucket, then the legacy flat stage dir (transition fallback). Non-existent
    entries are kept — callers skip directories that aren't present. Neither dir
    is created here; reads never have the side effect of making stage folders."""
    return [os.path.join(base_dir, bucket) + os.sep, base_dir]


def iter_stage_files(base_dir: str, bucket: str) -> Iterator[str]:
    """Yield absolute paths of a type's files in a stage, across the per-type
    bucket and the legacy flat layout (transition). Skips macOS AppleDouble
    sidecars and any entry that isn't a plain file (e.g. other types' buckets when
    scanning the flat dir). The single place the transition read-scan lives; the
    legacy flat dir drops out of ``read_dirs_for_bucket`` in the follow-up."""
    for directory in read_dirs_for_bucket(base_dir, bucket):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.startswith("._"):
                continue
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                yield full
