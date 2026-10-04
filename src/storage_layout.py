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

from src.settings import STORAGE_ROOT


def storage_is_mounted() -> bool:
    """Whether the storage volume is mounted (its root exists). Stage dirs live on
    an external volume; when it is unmounted the pipeline must fail loudly rather
    than write to the boot disk."""
    return os.path.isdir(STORAGE_ROOT)


def write_dir_for_bucket(base_dir: str, bucket: str) -> str:
    """The per-type subdirectory ``<base_dir>/<bucket>/`` to write into (always the
    new layout — the pipeline never writes flat), bootstrapping the stage tree.

    Created only when the storage volume is mounted (:func:`storage_is_mounted`):
    a mounted-but-empty volume self-bootstraps its stage dirs, but an unmounted one
    is left alone so the write fails loudly against the missing dir instead of
    fabricating the tree on the boot disk."""
    path = os.path.join(base_dir, bucket) + os.sep
    if storage_is_mounted():
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
