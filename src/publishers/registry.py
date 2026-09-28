"""Concrete publishers and the registry the workflow dispatches through.

Each publisher wraps an existing processor exactly as the old task bodies did, so
this is a pure refactor — the same processors run, in the same order, across the
same meeting types.
"""

import logging

from src.processors.archive_docs import DocumentArchiver
from src.processors.update_wiki import WikiUpdater
from src.processors.upload_transcript import TranscriptUploader
from src.processors.upload_video import VideoUploader
from src.publishers.base import Publisher
from src.types import MEETING_TYPE_BY_SOURCE

logger = logging.getLogger(__name__)


class VideoUploadPublisher(Publisher):
    name = "upload_video"
    destination = "youtube"

    def run(self) -> None:
        for source_type in MEETING_TYPE_BY_SOURCE:
            VideoUploader(source_type).process()


class TranscriptUploadPublisher(Publisher):
    name = "upload_transcript"
    destination = "google_docs"

    def run(self) -> None:
        for source_type in MEETING_TYPE_BY_SOURCE:
            TranscriptUploader(source_type).process()


class DocumentArchivePublisher(Publisher):
    name = "archive_docs"
    destination = "google_docs"

    def run(self) -> None:
        for meeting_type in MEETING_TYPE_BY_SOURCE.values():
            DocumentArchiver(meeting_type).process()


class WikiPublisher(Publisher):
    name = "update_wiki"
    destination = "wiki"

    def run(self) -> None:
        for source_type in MEETING_TYPE_BY_SOURCE:
            WikiUpdater(source_type).process()


# Registry keyed by stage name; the workflow dispatches through run_publisher.
PUBLISHERS: dict[str, Publisher] = {
    publisher.name: publisher
    for publisher in (
        VideoUploadPublisher(),
        TranscriptUploadPublisher(),
        DocumentArchivePublisher(),
        WikiPublisher(),
    )
}


def run_publisher(name: str) -> None:
    """Run a registered publisher if its destination is enabled, else skip it.

    Every destination is enabled by default, so this reproduces the previous
    behavior until a destination is turned off via DISABLED_PUBLISHERS (TRA-133
    will build out the fuller config).
    """
    publisher = PUBLISHERS[name]
    if not publisher.is_enabled():
        logger.info(
            "Skipping disabled publisher %r (destination %r)",
            name,
            publisher.destination,
        )
        return
    publisher.run()
