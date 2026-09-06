"""Raw-response snapshotting so a broken parser can be replayed.

Every scrape writes the raw page captures to disk *before* parsing them, keyed
by meeting id and fetch timestamp. If the CivicClerk markup changes and parsing
breaks, the parser can be fixed and re-run against the last-known-good capture
(see :func:`src.scrapers.cc_meetings.replay_files_from_snapshot`) instead of
losing that meeting's data permanently.

Layout under ``root``::

    _listing/<stamp>.html          # the meeting listing page
    <meeting_id>/<stamp>/files.html

Each ``MeetingRecord`` carries the ``"<meeting_id>/<stamp>"`` ref so a capture
can be located later.
"""

from datetime import datetime
from pathlib import Path

from src.scrapers.errors import TransientScrapeError

FETCH_STAMP_FORMAT = "%Y-%m-%dT%H%M%S"
LISTING_DIR = "_listing"


def fetch_stamp(moment: datetime) -> str:
    """A filesystem-safe stamp identifying a single scrape run."""
    return moment.strftime(FETCH_STAMP_FORMAT)


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure_ready(self) -> None:
        """Verify the snapshot root is usable, else raise a transient error.

        The parent (the shared "CC Meetings" dir) only exists when the external
        volume is mounted. Creating it with ``parents=True`` would silently
        shadow the mount point on the boot disk, so require the parent up front
        and only create the leaf.
        """
        if not self.root.parent.is_dir():
            raise TransientScrapeError(
                f"Storage volume not mounted: {self.root.parent} is missing; "
                f"cannot snapshot raw responses."
            )
        try:
            self.root.mkdir(exist_ok=True)
        except OSError as exc:
            raise TransientScrapeError(
                f"Cannot create snapshot directory {self.root}: {exc}"
            ) from exc

    def ref(self, meeting_id: str, stamp: str) -> str:
        return f"{meeting_id}/{stamp}"

    def write_listing(self, stamp: str, content: str) -> str:
        target = self.root / LISTING_DIR / f"{stamp}.html"
        self._write(target, content)
        return f"{LISTING_DIR}/{stamp}.html"

    def write(self, meeting_id: str, stamp: str, name: str, content: str) -> str:
        target = self.root / meeting_id / stamp / name
        self._write(target, content)
        return self.ref(meeting_id, stamp)

    def path_for(self, ref: str, name: str) -> Path:
        return self.root / ref / name

    def read(self, ref: str, name: str) -> str:
        return self.path_for(ref, name).read_text(encoding="utf-8")

    def _write(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
