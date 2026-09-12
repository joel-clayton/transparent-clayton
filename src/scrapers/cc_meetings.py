import json
import logging
import os
import re
import time
from datetime import date, datetime
from typing import TypedDict, Any, Callable, cast

from pydantic import ValidationError
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from celery_app import r
from src.constants import (
    AV_SEEN_KEY,
    DATE_FORMAT,
    DATETIME_FORMAT,
    DETAIL_KEY,
    NO_ASSETS_KEY,
)
from src.scrapers.constants import (
    CIVIC_CLERK_TZ,
    CIVIC_CLERK_URL,
    CLIP_ARG_REGEX,
    DATE_INPUT_FORMAT,
    DATETIME_INPUT_FORMAT,
    DOWNLOADED_PATH,
    LISTING_WAIT_TIMEOUT,
    MIN_RENDERED_BODY_CHARS,
    PAGE_LOAD_TIMEOUT,
    RAW_SNAPSHOT_PATH,
    SCRAPE_RETRY_ATTEMPTS,
    SCRAPE_RETRY_BASE_DELAY,
    SCRAPE_RETRY_DEADLINE,
    SCRAPE_RETRY_MAX_DELAY,
    SOURCE_URL,
    CIVIC_CLERK_START_DATE,
)
from src.scrapers.alerting import AlertLevel, alert
from src.scrapers.civic_clerk_api import (
    event_datetime,
    fetch_events,
    is_cancelled,
    matches_category,
    merge_by_datetime,
    split_documents,
    video_url,
)
from src.scrapers.errors import SiteStructureError, TransientScrapeError
from src.meeting_types import CITY_COUNCIL, MeetingType
from src.scrapers.models import MeetingRecord, PipelineClass
from src.scrapers.retry import retry_transient
from src.scrapers.snapshot import SnapshotStore, fetch_stamp
from src.scrapers.validate import url_resolves, url_resolves_best_effort
from src.types import Meeting
from src.util import get_date_or_datetime_string_from_string

logger = logging.getLogger(__name__)


def browser() -> WebDriver:
    driver = webdriver.Chrome()
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.get(SOURCE_URL)
    return driver


element_property_names = {
    "Name": "innerText",
    "Date": "innerText",  # dt parse
    "Duration": "innerText",
    "Agenda": "innerHTML",
    "Minutes and Supplemental Materials": "innerHTML",
    "Video": "innerHTML",
    "Agenda Packet": "innerHTML",
}


class CityCouncilMeetingRaw(TypedDict):
    Name: str
    Date: str
    Duration: str
    Agenda: str
    MinutesAndSupplementalMaterials: str
    Video: str
    AgendaPacket: str


class UrlObject(TypedDict):
    Name: str
    Url: str


ordered_header_fields = [
    "Name",
    "Date",
    "Duration",
    "Agenda",
    "Minutes and Supplemental Materials",
    "Video",
    "Agenda Packet",
]


def parse_date_input(date_str: str) -> datetime | date:
    try:
        return datetime.strptime(date_str, DATETIME_INPUT_FORMAT)
    except ValueError:
        return datetime.strptime(date_str, DATE_INPUT_FORMAT).date()


def parse_url_from_html(html_str: str, start: str = "//", end: str = '">') -> str:
    # if multiple matches, name the matches
    html_str = html_str.replace("&amp;", "&")
    matches = re.search(rf"{start}(.*?){end}", html_str)
    if matches:
        return "https://" + matches.group(1)
    return ""


def parse_multiple_urls_from_html(
    html_str: str, start: str | None = None, end: str | None = None
) -> dict:
    matches = re.findall(rf"{start}.*?{end}", html_str)
    named_urls = {}
    for match in matches:
        url_name = get_inner_text_from_html(match)
        url = parse_url_from_html(match)
        named_urls[url_name] = url
    return named_urls


def get_inner_text_from_html(html_str: str) -> str:
    matches = re.findall(r">(.*?)</", html_str)
    if matches:
        return matches[0]
    return ""


def get_clip_id_from_url(url: str) -> str | None:
    matches = re.search(CLIP_ARG_REGEX, url)
    if matches:
        return matches.group(0).split("=")[1]
    else:
        return None


element_parser = {
    "Date": (parse_date_input, {}),
    "Agenda": (
        parse_url_from_html,
        {"start": "//", "end": '"target'},
    ),
    "Minutes and Supplemental Materials": (
        parse_multiple_urls_from_html,
        {"start": "//", "end": "</option>"},
    ),
    "Video": (
        parse_url_from_html,
        {"start": "//", "end": "','"},
    ),
    "Agenda Packet": (
        parse_url_from_html,
        {"start": 'href="', "end": '"target'},
    ),
}


def get_latest_downloaded_date(meeting_type: MeetingType = CITY_COUNCIL) -> str:
    # A missing directory means the external volume isn't mounted (transient),
    # which is distinct from a mounted-but-empty directory (a legit "" result).
    if not os.path.isdir(DOWNLOADED_PATH):
        raise TransientScrapeError(
            f"Downloads volume not mounted or missing: {DOWNLOADED_PATH}"
        )
    # Filter by this type's filename stub so each type has its own watermark
    # (all types share the downloads dir, disambiguated by stub).
    filenames = os.listdir(DOWNLOADED_PATH)
    time_sorted_filenames = sorted(
        [
            filename
            for filename in filenames
            if filename.endswith(".mp4") and meeting_type.file_stub in filename
        ],
        reverse=True,
    )
    if not time_sorted_filenames:
        return ""
    date_keyed_filenames = {}
    for filename in time_sorted_filenames:
        key = get_date_or_datetime_string_from_string(filename)
        if key:
            date_keyed_filenames[key] = filename
    date_sorted_filenames = sorted(date_keyed_filenames.keys(), reverse=True)
    if not date_sorted_filenames:
        return ""
    return date_sorted_filenames[0]


def switch_into_granicus_iframe(driver: WebDriver, timeout: int = 15) -> None:
    """Switch the driver into the Granicus iframe.

    The source page hosts several iframes (CivicClerk, Granicus, Google Maps).
    Selecting by tag-name landed on the wrong vendor when the page added a
    CivicClerk frame; filter by src to be specific.
    """
    iframe = WebDriverWait(driver, timeout).until(
        lambda d: next(
            (
                f
                for f in d.find_elements(By.TAG_NAME, "iframe")
                if "granicus" in (f.get_attribute("src") or "")
            ),
            None,
        )
    )
    driver.switch_to.frame(iframe)


def find_meetings_panel(driver: WebDriver, heading: str) -> tuple[Any, list[str]]:
    """Find the CollapsiblePanel whose tab heading matches `heading`, and
    return its content element plus the validated header row.

    Several panels on the page share the same column layout, so matching by
    headers alone is ambiguous — we locate by tab text and then sanity-check
    the headers against ordered_header_fields.
    """
    panels = driver.find_elements(By.CSS_SELECTOR, "[id^='CollapsiblePanel']")
    seen_headings: list[str] = []
    for panel in panels:
        try:
            tab = panel.find_element(By.CLASS_NAME, "CollapsiblePanelTab")
        except NoSuchElementException:
            continue
        tab_text = tab.get_property("innerText").strip()
        seen_headings.append(tab_text)
        if tab_text != heading:
            continue
        content = panel.find_element(By.CLASS_NAME, "CollapsiblePanelContent")
        for thead in content.find_elements(By.TAG_NAME, "thead"):
            header_cells = thead.find_elements(By.XPATH, ".//th")
            values = [cell.get_property("innerText") for cell in header_cells]
            if values == ordered_header_fields:
                return content, values
        raise NoSuchElementException(
            f"Panel {heading!r} found, but no thead matched expected headers "
            f"{ordered_header_fields!r}."
        )
    raise NoSuchElementException(
        f"No CollapsiblePanel with heading {heading!r}. Found headings: {seen_headings!r}"
    )


_civic_clerk_logger = logging.getLogger(f"{__name__}::civic_clerk")


def _civic_clerk_browser() -> WebDriver:
    """Headless Chrome with a real-looking user-agent.

    The CivicClerk SPA refused to hydrate under default headless settings during
    development; a desktop UA string made it render reliably.
    """
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,1200")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def _scrape_civic_clerk_meeting_files(
    driver: WebDriver, meeting_id: str
) -> dict[str, str]:
    """Return {file_label: url} for a meeting's Files tab. Empty if no files.

    Most files only have a signed blob URL (~1 week TTL); only the Agenda Packet
    sometimes has a stable /GetMeetingFileStream API URL. We capture whichever
    is present per row. URLs are paired with download buttons by document order
    (each file row has exactly one button and one useful anchor).
    """
    driver.get(f"{CIVIC_CLERK_URL}event/{meeting_id}/files")
    try:
        WebDriverWait(driver, 15).until(
            lambda d: (
                "Meeting Files"
                in d.find_element(By.TAG_NAME, "body").get_property("innerText")
            )
        )
    except TimeoutException:
        return {}
    if "No published Meeting Files" in driver.find_element(
        By.TAG_NAME, "body"
    ).get_property("innerText"):
        return {}

    # File rows hydrate progressively. Wait for the button count to hold
    # steady across several consecutive polls before reading — a single repeat
    # isn't enough because intermediate render states can briefly show a
    # partial list.
    selector = "button[aria-label^='Download ']"
    last_count = -1
    stable = 0
    for _ in range(20):
        time.sleep(1)
        count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        if count == last_count and count > 0:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last_count = count

    return _extract_files_from_dom(driver)


def _extract_files_from_dom(driver: WebDriver) -> dict[str, str]:
    """Pair download buttons with their file anchors on the current DOM.

    Split out from navigation/hydration so the same extraction runs against a
    live page or a replayed snapshot loaded via ``file://``.
    """
    selector = "button[aria-label^='Download ']"
    download_buttons = driver.find_elements(By.CSS_SELECTOR, selector)
    file_anchors = driver.find_elements(
        By.CSS_SELECTOR,
        "a[href*='GetMeetingFileStream'], a[href*='blob.core.windows.net']",
    )
    files: dict[str, str] = {}
    for btn, link in zip(download_buttons, file_anchors):
        label = (
            (btn.get_attribute("aria-label") or "").removeprefix("Download ").strip()
        )
        href = link.get_attribute("href") or ""
        if not (label and href):
            continue
        # Multiple files can share a label ("Staff Report" appears once per
        # agenda item); suffix duplicates to preserve them all.
        key = label
        n = 2
        while key in files:
            key = f"{label} ({n})"
            n += 1
        files[key] = href
    return files


_JW_PLAYLIST_JS = """
try {
    if (typeof jwplayer !== 'function') return null;
    for (let i = 0; i < 5; i++) {
        try {
            const p = jwplayer(i);
            if (p && p.getPlaylist) {
                const pl = p.getPlaylist();
                if (pl && pl.length) return pl[0].file || null;
            }
        } catch(e) {}
    }
    return null;
} catch(e) { return null; }
"""


def _scrape_civic_clerk_meeting_video(driver: WebDriver, meeting_id: str) -> str:
    """Return the MP4 URL from a meeting's Media tab, or '' if no video.

    The <video> element renders without a src because JW Player uses
    preload="none" — the source only attaches on play. The URL is available
    via JW Player's JS playlist API though, so read it there.
    """
    driver.get(f"{CIVIC_CLERK_URL}event/{meeting_id}/media")
    try:
        WebDriverWait(driver, 20).until(
            lambda d: (
                d.execute_script(_JW_PLAYLIST_JS)
                or "no video"
                in d.find_element(By.TAG_NAME, "body").get_property("innerText").lower()
            )
        )
    except TimeoutException:
        return ""
    return driver.execute_script(_JW_PLAYLIST_JS) or ""


LISTING_SELECTOR = "li.meeting-event a[data-id]"


def _page_rendered(driver: WebDriver) -> bool:
    """True if the body has rendered a non-trivial amount of text.

    Used to tell "the SPA rendered but our selector is gone" (structure change)
    from "the page never loaded" (transient).
    """
    try:
        body = driver.find_element(By.TAG_NAME, "body").get_property("innerText")
    except (NoSuchElementException, WebDriverException):
        return False
    return len(str(body or "").strip()) > MIN_RENDERED_BODY_CHARS


def _load_meeting_listing(driver: WebDriver) -> None:
    """Load the CivicClerk portal and wait for the meeting listing to hydrate.

    Raises :class:`TransientScrapeError` on a load/wait timeout where the page
    is still blank (retryable), and :class:`SiteStructureError` when the page
    rendered but nothing matches ``LISTING_SELECTOR`` (the markup changed).
    """
    try:
        driver.get(CIVIC_CLERK_URL)
    except (TimeoutException, WebDriverException) as exc:
        raise TransientScrapeError(
            f"Timed out loading {CIVIC_CLERK_URL}: {exc}"
        ) from exc
    try:
        WebDriverWait(driver, LISTING_WAIT_TIMEOUT).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, LISTING_SELECTOR)
        )
    except TimeoutException as exc:
        if _page_rendered(driver):
            raise SiteStructureError(
                f"CivicClerk portal rendered but no elements matched "
                f"{LISTING_SELECTOR!r}; the meeting listing markup may have changed."
            ) from exc
        raise TransientScrapeError(
            f"CivicClerk meeting listing did not load in {LISTING_WAIT_TIMEOUT}s: {exc}"
        ) from exc


def _try_snapshot(operation: Callable[[], str], description: str) -> str | None:
    """Run a snapshot write, warning (not failing the run) on an IO error.

    The mount guard already confirmed the volume; an individual write failure
    should cost us the replay net for one page, not the whole scrape.
    """
    try:
        return operation()
    except OSError as exc:
        _civic_clerk_logger.warning("Could not snapshot %s: %s", description, exc)
        return None


def replay_files_from_snapshot(
    ref: str, name: str = "files.html", store: SnapshotStore | None = None
) -> dict[str, str]:
    """Re-parse a meeting's files from its saved snapshot.

    Recovery path for after a site change breaks the live parser: fix
    :func:`_extract_files_from_dom`, then replay the last-known-good capture
    (loaded via ``file://``) instead of losing the meeting's data.
    """
    store = store or SnapshotStore(RAW_SNAPSHOT_PATH)
    snapshot_path = store.path_for(ref, name)
    driver = _civic_clerk_browser()
    try:
        driver.get(snapshot_path.as_uri())
        return _extract_files_from_dom(driver)
    finally:
        driver.quit()


def _av_seen(meeting_type: MeetingType, meeting_id: str) -> bool:
    """Whether this meeting's video was already handed off to the A/V pipeline."""
    return bool(r.sismember(meeting_type.redis_key(AV_SEEN_KEY), meeting_id))


def _mark_av_seen(meeting_type: MeetingType, meeting_id: str) -> None:
    """Record a meeting's video as handed off so future runs skip re-scraping it."""
    r.sadd(meeting_type.redis_key(AV_SEEN_KEY), meeting_id)


def _mark_no_assets(meeting_type: MeetingType, meeting_key: str) -> None:
    """Record a meeting as having no published materials (transparency page)."""
    r.sadd(meeting_type.redis_key(NO_ASSETS_KEY), meeting_key)


def _clear_no_assets(meeting_type: MeetingType, meeting_key: str) -> None:
    """Drop a meeting from the no-assets set once it has published something."""
    r.srem(meeting_type.redis_key(NO_ASSETS_KEY), meeting_key)


def _warn_unresolved_docs(record: MeetingRecord, meeting_key: str) -> None:
    """Best-effort HEAD-check of document links; warn but never block handoff."""
    for label, doc_url in (record.minutes_and_supplemental_materials or {}).items():
        if not url_resolves_best_effort(doc_url):
            _civic_clerk_logger.warning(
                "Meeting %s document %r did not resolve: %s",
                meeting_key,
                label,
                doc_url,
            )
    if record.agenda_packet and not url_resolves_best_effort(record.agenda_packet):
        _civic_clerk_logger.warning(
            "Meeting %s agenda packet did not resolve: %s",
            meeting_key,
            record.agenda_packet,
        )


def parse_meetings_from_civic_clerk_iframe(
    latest_date: datetime, meeting_type: MeetingType = CITY_COUNCIL
) -> list[Meeting]:
    """Fetch past meetings of one type from the CivicClerk OData API.

    Returns Meetings whose date is strictly after `latest_date` and strictly
    before now. Filters by ``meeting_type.category`` (the exact CivicClerk
    ``categoryName``) so only that type's meetings are kept.

    (The name is historical — this used to scrape the portal's DOM, which only
    exposed *upcoming* events and so missed past meetings in a lookback range.
    It now queries the underlying API via :mod:`src.scrapers.civic_clerk_api`;
    retiring the leftover Selenium/DOM code is tracked in issue #28.)

    latest_date is treated as naive local time, matching the format the rest of
    the scraper uses. CivicClerk's ``startDateTime`` is labeled with a 'Z' suffix
    but is really wall-clock local time (e.g. "2026-05-19T19:00:00Z" is 7:00 PM
    PDT), so we strip the timezone rather than convert.
    """
    store = SnapshotStore(RAW_SNAPSHOT_PATH)
    new_meetings: list[Meeting] = []
    # Fail fast (and transiently) if the snapshot volume isn't mounted.
    store.ensure_ready()
    now_local = datetime.now(CIVIC_CLERK_TZ).replace(tzinfo=None)
    stamp = fetch_stamp(now_local)

    events = retry_transient(
        lambda: fetch_events(latest_date, now_local),
        label=f"fetch CivicClerk events for {meeting_type.display_name}",
        attempts=SCRAPE_RETRY_ATTEMPTS,
        base_delay=SCRAPE_RETRY_BASE_DELAY,
        max_delay=SCRAPE_RETRY_MAX_DELAY,
        deadline=SCRAPE_RETRY_DEADLINE,
    )
    # Snapshot the raw API response so a later parser change can be replayed.
    _try_snapshot(
        lambda: store.write("_events", stamp, "events.json", json.dumps(events)),
        "events response",
    )

    # This type's events, bounded exactly (the API filter is only a narrowing),
    # then merged to one event per meeting datetime (the portal can split a
    # meeting's assets — or an empty duplicate — across same-time records).
    targets = merge_by_datetime(
        [
            event
            for event in events
            if matches_category(event, meeting_type)
            and latest_date < event_datetime(event) < now_local
        ]
    )
    _civic_clerk_logger.info(
        f"Found {len(targets)} past {meeting_type.display_name} "
        f"meeting(s) after {latest_date}"
    )

    quarantined: list[str] = []
    for event in targets:
        data_id = str(event.get("id") or "")
        meeting_dt = event_datetime(event)
        # Idempotency: skip meetings whose video was already handed off to the
        # A/V pipeline. Meetings without a handed-off video (docs-only, or
        # awaiting a video) are re-examined each run so a later-posted video is
        # still picked up.
        if _av_seen(meeting_type, data_id):
            _civic_clerk_logger.debug(
                "Skipping meeting %s (%s); video already handed off",
                data_id,
                meeting_dt,
            )
            continue
        # Capture this event's raw JSON so a broken parser can be replayed.
        snapshot_ref = _try_snapshot(
            lambda: store.write(data_id, stamp, "event.json", json.dumps(event)),
            f"event {data_id}",
        )
        meeting_key = meeting_dt.strftime(DATETIME_FORMAT)
        video_link = video_url(event)
        agenda_packet, supplemental = split_documents(event)
        try:
            record = MeetingRecord(
                key=meeting_key,
                duration="",
                agenda=agenda_packet,
                agenda_packet=agenda_packet,
                minutes_and_supplemental_materials=supplemental or None,
                video=video_link,
                clip_id=data_id,
                source_type=meeting_type.source_type,
                cancelled=is_cancelled(event),
                scraped_at=now_local.isoformat(timespec="seconds"),
                snapshot_ref=snapshot_ref,
            )
        except ValidationError as exc:
            quarantined.append(f"{meeting_key}: failed validation ({exc})")
            _civic_clerk_logger.debug(
                "Quarantined meeting %s; failed validation: %s", meeting_key, exc
            )
            continue

        # NO_ASSETS: nothing published — record for the transparency page and
        # move on. Not persisted to detail and not marked A/V-seen, so if the
        # city later posts materials a future run reclassifies it.
        if record.pipeline_class is PipelineClass.NO_ASSETS:
            _mark_no_assets(meeting_type, meeting_key)
            _civic_clerk_logger.debug(
                "No published materials for meeting %s (%s)", data_id, meeting_dt
            )
            continue

        # Has assets, so it must not linger on the transparency page.
        _clear_no_assets(meeting_type, meeting_key)

        # FULL: the video must actually resolve before A/V handoff. A network
        # failure raises TransientScrapeError (retried, then bubbles up to
        # alert); a real error status quarantines just this record.
        if record.pipeline_class is PipelineClass.FULL:
            video_ok = retry_transient(
                lambda: url_resolves(record.video),
                label=f"resolve video URL for {meeting_key}",
                attempts=SCRAPE_RETRY_ATTEMPTS,
                base_delay=SCRAPE_RETRY_BASE_DELAY,
                max_delay=SCRAPE_RETRY_MAX_DELAY,
                deadline=SCRAPE_RETRY_DEADLINE,
            )
            if not video_ok:
                quarantined.append(f"{meeting_key}: video URL not reachable")
                _civic_clerk_logger.debug(
                    "Quarantined meeting %s; video URL returned an error: %s",
                    meeting_key,
                    record.video,
                )
                continue

        _warn_unresolved_docs(record, meeting_key)

        meeting = cast(Meeting, record.model_dump(mode="json"))
        r.hset(
            meeting_type.redis_key(DETAIL_KEY),
            mapping={meeting_key: json.dumps(meeting)},
        )
        if record.pipeline_class is PipelineClass.FULL:
            # Only video meetings drive the A/V pipeline; mark handed off so
            # re-runs skip re-scraping. The task routes FULL keys (only) into
            # the scraped list.
            _mark_av_seen(meeting_type, data_id)
        # Return FULL and DOCS_ONLY meetings: both reach the wiki stage, and
        # the task filters FULL for the A/V pipeline.
        new_meetings.append(meeting)

    if quarantined:
        # One batched summary instead of per-record noise on every run.
        alert(
            AlertLevel.WARNING,
            f"{len(quarantined)} CivicClerk meeting(s) skipped/quarantined "
            "this run:\n- " + "\n- ".join(quarantined),
        )
    return new_meetings


def parse_meetings_from_url(
    latest_date: datetime, meeting_type: MeetingType = CITY_COUNCIL
) -> list[Meeting]:
    # >= so a watermark exactly at the CivicClerk cutover uses the API, not
    # Granicus. This matters for a brand-new meeting type: its fallback watermark
    # is NEW_TYPE_SCRAPE_START == CIVIC_CLERK_START_DATE, and such types have no
    # Granicus history — routing them to the (Selenium) Granicus path would
    # scrape the wrong source and fail. Granicus is reached only for a watermark
    # strictly before the cutover (genuine pre-CivicClerk backfill).
    if latest_date >= CIVIC_CLERK_START_DATE:
        logger.info(
            f"Latest date {latest_date} is on or after {CIVIC_CLERK_START_DATE}, "
            "skipping Granicus workflow"
        )
        return parse_meetings_from_civic_clerk_iframe(latest_date, meeting_type)
    else:
        logger.info(
            f"Latest date {latest_date} is before {CIVIC_CLERK_START_DATE}, using Granicus workflow"
        )
        new_meetings: list[Meeting] = []
        driver = browser()
        try:
            switch_into_granicus_iframe(driver)
            cc_panel_elem, header_cell_values = find_meetings_panel(
                driver, meeting_type.title_match
            )

            # Inspect and parse table body fields
            table_bodies = cc_panel_elem.find_elements(By.TAG_NAME, "tbody")
            for index, table_body in enumerate(table_bodies):
                body_rows = table_body.find_elements(By.XPATH, ".//tr")
                for body_row in body_rows:
                    body_cells = body_row.find_elements(By.XPATH, ".//td")
                    structured_raw_data = {}
                    for body_cell, header_cell_name in zip(
                        body_cells, header_cell_values
                    ):
                        property_name = element_property_names.get(header_cell_name)
                        body_cell_value = (
                            body_cell.get_property(property_name)
                            .replace("\n", "")
                            .replace(" ", "")
                            .replace("\xa0", " ")
                        )

                        extra_parser, kwargs = element_parser.get(
                            header_cell_name, (None, None)
                        )
                        if extra_parser:
                            parsed_value = extra_parser(body_cell_value, **kwargs)  # type: ignore
                        else:
                            parsed_value = body_cell_value
                        structured_raw_data[header_cell_name] = parsed_value
                    clip_id = get_clip_id_from_url(structured_raw_data.get("Video", ""))

                    if clip_id:
                        structured_raw_data["ClipId"] = clip_id
                    else:
                        raise AttributeError(
                            f"No clip_id found for cc_meeting: {structured_raw_data}"
                        )

                    parsed_date = structured_raw_data["Date"]
                    # parsed_date is datetime | date (from parse_date_input);
                    # latest_date is datetime, and Python refuses to compare
                    # the two directly, so normalize date → midnight datetime.
                    comparable_date = (
                        parsed_date
                        if isinstance(parsed_date, datetime)
                        else datetime.combine(parsed_date, datetime.min.time())
                    )
                    if comparable_date <= latest_date:
                        logger.debug(f"SKIPPING {parsed_date}")
                        continue

                    output_format = (
                        DATETIME_FORMAT
                        if isinstance(parsed_date, datetime)
                        else DATE_FORMAT
                    )
                    meeting_key = parsed_date.strftime(output_format)
                    structured_raw_data["Date"] = meeting_key
                    try:
                        record = MeetingRecord(
                            key=structured_raw_data.get("Date") or "",
                            duration=structured_raw_data.get("Duration") or "",
                            agenda=structured_raw_data.get("Agenda") or "",
                            agenda_packet=structured_raw_data.get("AgendaPacket") or "",
                            minutes_and_supplemental_materials=structured_raw_data.get(
                                "MinutesAndSupplementalMaterials"
                            ),
                            video=structured_raw_data.get("Video") or "",
                            clip_id=structured_raw_data.get("ClipId") or "",
                            source_type=meeting_type.source_type,
                        )
                    except ValidationError as exc:
                        logger.warning(
                            "Quarantined Granicus meeting %s; failed validation: %s",
                            meeting_key,
                            exc,
                        )
                        continue
                    meeting_details = cast(Meeting, record.model_dump(mode="json"))
                    r.hset(
                        meeting_type.redis_key(DETAIL_KEY),
                        mapping={meeting_key: json.dumps(meeting_details)},
                    )
                    new_meetings.append(meeting_details)
        finally:
            driver.quit()
        new_meetings += parse_meetings_from_civic_clerk_iframe(
            latest_date, meeting_type
        )
    return new_meetings
