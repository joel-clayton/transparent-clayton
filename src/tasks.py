import json
import logging

from celery import chain
from celery.exceptions import Ignore
from celery.schedules import crontab

from celery_app import app, r
from src.constants import SCRAPED_KEY, EXITED_EARLY
from src.meeting_types import MeetingType
from src.processors.archive_docs import DocumentArchiver
from src.processors.compress import Compressor
from src.processors.download import Downloader
from src.processors.extract import Extractor
from src.processors.transcribe import Transcriber
from src.processors.update_wiki import WikiUpdater
from src.scrapers.cc_meetings import get_latest_downloaded_date, parse_meetings_from_url
from src.scrapers.alerting import AlertLevel, alert
from src.scrapers.constants import CIVIC_CLERK_START_DATE
from src.scrapers.errors import SiteStructureError, TransientScrapeError
from src.scrapers.models import PipelineClass
from src.processors.upload_transcript import TranscriptUploader
from src.processors.upload_video import VideoUploader
from src.types import MEETING_TYPE_BY_SOURCE, Meeting
from src.util import get_datetime_from_string, send_to_discord_bots

logger = logging.getLogger(__name__)

# Fallback scrape watermark for a meeting type with no downloads yet (a brand-new
# type has no file to derive "latest downloaded" from). CivicClerk-era start.
NEW_TYPE_SCRAPE_START = CIVIC_CLERK_START_DATE


def _scrape_type_for_download(meeting_type: MeetingType) -> list[Meeting]:
    """Scrape one meeting type, publishing its FULL (video) dates to the A/V
    worklist. Returns the meetings found (FULL + DOCS_ONLY) this run."""
    latest_date_str = get_latest_downloaded_date(meeting_type)
    if latest_date_str:
        latest_date = get_datetime_from_string(latest_date_str)
        if latest_date is None:
            raise Exception(
                f"Could not parse a date from {latest_date_str!r}; skipping"
            )
    else:
        # No downloads yet for this type — scrape from the fallback start so it
        # bootstraps (and catches up any backlog) rather than never running.
        latest_date = NEW_TYPE_SCRAPE_START
        logger.info(
            "No downloaded %s meetings yet; scraping from %s",
            meeting_type.display_name,
            latest_date,
        )
    meetings = parse_meetings_from_url(latest_date, meeting_type)
    # Only FULL (video) meetings drive the A/V pipeline; docs-only meetings are
    # handled by the wiki/archival stages and must never be downloaded.
    video_dates = sorted(
        m["key"]
        for m in meetings
        if m.get("pipeline_class") == PipelineClass.FULL.value
        and (parsed := get_datetime_from_string(m["key"])) is not None
        and parsed > latest_date
    )
    r.set(meeting_type.redis_key(SCRAPED_KEY), json.dumps(video_dates))
    return meetings


@app.task()
def get_cc_meeting_details_for_download() -> None:
    try:
        # Scrape each configured meeting type into its own worklist. Inside the
        # try so an unmounted volume (get_latest_downloaded_date raises) surfaces
        # as a transient error rather than crashing uncaught.
        found_any = False
        for meeting_type in MEETING_TYPE_BY_SOURCE.values():
            meetings = _scrape_type_for_download(meeting_type)
            logger.info(
                "%s meetings to process: %s", meeting_type.display_name, meetings
            )
            if meetings:
                found_any = True
        if not found_any:
            raise Ignore(EXITED_EARLY)
        return
    except Ignore:
        # "No new meetings" (EXITED_EARLY) is the normal empty result — stay quiet.
        logger.info("No new meetings to process; exiting quietly")
        raise
    except SiteStructureError as e:
        # The page loaded but the parser found nothing it recognised — retrying
        # won't help, a human needs to look at the changed markup.
        alert(
            AlertLevel.ACTIONABLE,
            f"Site structure changed; scraper needs attention: {e}",
        )
        raise Ignore(f"Site structure changed; scraper needs attention: {e}")
    except TransientScrapeError as e:
        # Network/WebDriver flakiness that survived the retry ceiling.
        alert(AlertLevel.ACTIONABLE, f"Transient scrape failure after retries: {e}")
        raise Ignore(f"Transient scrape failure after retries: {e}")
    except Exception as e:
        alert(AlertLevel.ACTIONABLE, f"Unexpected scraper failure: {e}")
        raise Ignore(f"Something has gone pear-shaped: {e}")


# Each stage runs once per configured meeting type. Processors keyed by the
# SourceType enum take it directly; the scraper-side archiver takes a MeetingType.
@app.task
def download_cc_meeting_video() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        Downloader(source_type).process()


@app.task
def compress_cc_meeting_video() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        Compressor(source_type).process()


@app.task
def upload_cc_meeting_video() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        VideoUploader(source_type).process()


@app.task
def extract_cc_meeting_audio() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        Extractor(source_type).process()


@app.task
def transcribe_cc_meeting_audio() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        Transcriber(source_type).process()


@app.task
def upload_cc_meeting_transcript() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        TranscriptUploader(source_type).process()


@app.task
def archive_cc_meeting_docs() -> None:
    for meeting_type in MEETING_TYPE_BY_SOURCE.values():
        DocumentArchiver(meeting_type).process()


@app.task
def update_cc_mtg_wiki() -> None:
    for source_type in MEETING_TYPE_BY_SOURCE:
        WikiUpdater(source_type).process()


@app.task
def notify_success() -> None:
    logger.info("Completed processing 🎉")


@app.task
def cc_meeting_workflow() -> None:
    workflow.apply_async()


@app.task
def log_error(request: object, exc: BaseException, traceback: object) -> None:
    print(f"request: {request}, exc: {exc}, traceback: {traceback}")
    for message in (
        f"REQUEST: {request}",
        f"EXCEPTION: {exc}",
        f"TRACEBACK: {traceback}",
    ):
        if EXITED_EARLY not in exc.args:
            send_to_discord_bots(message)
            return
    logger.info("No new meetings to process, exiting")


workflow = chain(
    get_cc_meeting_details_for_download.si().on_error(log_error.s()),
    download_cc_meeting_video.si().on_error(log_error.s()),
    compress_cc_meeting_video.si().on_error(log_error.s()),
    upload_cc_meeting_video.si().on_error(log_error.s()),
    extract_cc_meeting_audio.si().on_error(log_error.s()),
    transcribe_cc_meeting_audio.si().on_error(log_error.s()),
    upload_cc_meeting_transcript.si().on_error(log_error.s()),
    archive_cc_meeting_docs.si().on_error(log_error.s()),
    update_cc_mtg_wiki.si().on_error(log_error.s()),
    notify_success.si().on_error(log_error.s()),
)


app.conf.beat_schedule = {
    "cc-meeting-workflow": {
        "task": "src.tasks.cc_meeting_workflow",
        "schedule": crontab(hour="*,8-18", minute=0),
    },
}
