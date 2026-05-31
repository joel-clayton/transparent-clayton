import json
import logging
import os
import re
import time
from datetime import date, datetime
from typing import TypedDict, Any
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from celery_app import r
from src.constants import DATE_FORMAT, DATETIME_FORMAT, DETAIL_CC_MTG_KEY
from src.scrapers.constants import (
    CIVIC_CLERK_URL,
    CLIP_ARG_REGEX,
    DATE_INPUT_FORMAT,
    DATETIME_INPUT_FORMAT,
    DOWNLOADED_PATH,
    SOURCE_URL,
    CIVIC_CLERK_START_DATE,
)
from src.types import Meeting
from src.util import get_date_or_datetime_string_from_string

logger = logging.getLogger(__name__)


def browser() -> WebDriver:
    driver = webdriver.Chrome()
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


def get_latest_downloaded_date() -> str:
    filenames = os.listdir(DOWNLOADED_PATH)
    time_sorted_filenames = sorted(
        [filename for filename in filenames if filename.endswith(".mp4")], reverse=True
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


CIVIC_CLERK_TZ = ZoneInfo("America/Los_Angeles")
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
    return webdriver.Chrome(options=opts)


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


def parse_meetings_from_civic_clerk_iframe(latest_date: datetime) -> list[Meeting]:
    """Scrape past City Council meetings from the CivicClerk portal.

    Returns Meetings whose date is strictly after `latest_date` and strictly
    before now. Filters by the literal title substring "City Council" so
    Planning Commission, Trails, etc. are excluded.

    latest_date is treated as naive local time, matching the format the rest
    of the scraper code uses. Note: CivicClerk's data-date attribute is labeled
    with a 'Z' suffix but the front-end renders it as wall-clock local time
    (e.g. data-date "2026-05-19T19:00:00Z" displays as "7:00 PM PDT"), so we
    strip the timezone rather than convert.
    """
    driver = _civic_clerk_browser()
    new_meetings: list[Meeting] = []
    try:
        driver.get(CIVIC_CLERK_URL)
        WebDriverWait(driver, 30).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, "li.meeting-event a[data-id]")
        )
        # Snapshot the listing before we navigate away; element references go
        # stale once we leave the page.
        targets: list[tuple[str, datetime]] = []
        now_local = datetime.now(CIVIC_CLERK_TZ).replace(tzinfo=None)
        for a in driver.find_elements(By.CSS_SELECTOR, "li.meeting-event a[data-id]"):
            title = a.get_property("innerText") or ""
            if "City Council" not in title:
                continue
            data_id = a.get_attribute("data-id") or ""
            data_date = a.get_attribute("data-date") or ""
            if not (data_id and data_date):
                continue
            meeting_dt = datetime.fromisoformat(
                data_date.replace("Z", "+00:00")
            ).replace(tzinfo=None)
            if meeting_dt <= latest_date or meeting_dt >= now_local:
                continue
            targets.append((data_id, meeting_dt))

        _civic_clerk_logger.info(
            f"Found {len(targets)} past City Council meeting(s) after {latest_date}"
        )

        for data_id, meeting_dt in targets:
            files = _scrape_civic_clerk_meeting_files(driver, data_id)
            video_url = _scrape_civic_clerk_meeting_video(driver, data_id)
            meeting_key = meeting_dt.strftime(DATETIME_FORMAT)
            agenda_packet = files.pop("Agenda Packet", "")
            meeting: Meeting = {
                "key": meeting_key,
                "duration": "",
                "agenda": agenda_packet,
                "agenda_packet": agenda_packet,
                "minutes_and_supplemental_materials": files or None,
                "video": video_url,
                "clip_id": data_id,
                "source_type": "city_council_meeting",
            }
            r.hset(
                DETAIL_CC_MTG_KEY,
                mapping={meeting_key: json.dumps(meeting)},
            )
            new_meetings.append(meeting)
    finally:
        driver.quit()
    return new_meetings


def parse_meetings_from_url(latest_date: datetime) -> list[Meeting]:
    if latest_date > CIVIC_CLERK_START_DATE:
        return parse_meetings_from_civic_clerk_iframe(latest_date)
    else:
        new_meetings: list[Meeting] = []
        driver = browser()
        try:
            switch_into_granicus_iframe(driver)
            cc_panel_elem, header_cell_values = find_meetings_panel(
                driver, "City Council"
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
                    meeting_details: Meeting = {
                        "key": structured_raw_data.get("Date") or "",
                        "duration": structured_raw_data.get("Duration") or "",
                        "agenda": structured_raw_data.get("Agenda") or "",
                        "agenda_packet": structured_raw_data.get("AgendaPacket") or "",
                        "minutes_and_supplemental_materials": structured_raw_data.get(
                            "MinutesAndSupplementalMaterials"
                        ),
                        "video": structured_raw_data.get("Video") or "",
                        "clip_id": structured_raw_data.get("ClipId") or "",
                        "source_type": "city_council_meeting",
                    }
                    r.hset(
                        DETAIL_CC_MTG_KEY,
                        mapping={meeting_key: json.dumps(meeting_details)},
                    )
                    new_meetings.append(meeting_details)
        finally:
            driver.quit()
        new_meetings += parse_meetings_from_civic_clerk_iframe(latest_date)
    return new_meetings
