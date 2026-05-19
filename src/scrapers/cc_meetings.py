import json
import os
import re
from datetime import date, datetime
from typing import TypedDict, Any

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from celery_app import r
from src.constants import DATE_FORMAT, DATETIME_FORMAT, DETAIL_CC_MTG_KEY
from src.scrapers.constants import (
    CLIP_ARG_REGEX,
    DATE_INPUT_FORMAT,
    DATETIME_INPUT_FORMAT,
    DEFAULT_ONE_WEEK_SECONDS_EXPIRATION,
    DOWNLOADED_PATH,
    SOURCE_URL,
    VIDEO_DATE_REGEX,
    VIDEO_FILE_NAME_REGEX,
)


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


class CityCouncilMeeting(TypedDict):
    Name: str
    Date: str
    Duration: str
    Agenda: str
    MinutesAndSupplementalMaterials: dict
    Video: str
    AgendaPacket: str
    ClipId: str


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
        file_match = re.search(VIDEO_FILE_NAME_REGEX, filename)
        if file_match:
            date_match = re.search(VIDEO_DATE_REGEX, filename)
            if date_match:
                date_keyed_filenames[date_match.group(0)] = filename
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


def parse_meetings_from_url(latest_date: str) -> dict[str, dict[str, Any]]:
    driver = browser()
    switch_into_granicus_iframe(driver)
    cc_panel_elem, header_cell_values = find_meetings_panel(driver, "City Council")

    # Inspect and parse table body fields
    cc_meetings: dict[str, dict[str, Any]] = {}
    table_bodies = cc_panel_elem.find_elements(By.TAG_NAME, "tbody")
    for index, table_body in enumerate(table_bodies):
        body_rows = table_body.find_elements(By.XPATH, ".//tr")
        for body_row in body_rows:
            body_cells = body_row.find_elements(By.XPATH, ".//td")
            structured_raw_data = {}
            for body_cell, header_cell_name in zip(body_cells, header_cell_values):
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
            # print(f"parsed date: {structured_raw_data['Date']}")
            parsed_date = structured_raw_data["Date"]
            output_format = (
                DATETIME_FORMAT if isinstance(parsed_date, datetime) else DATE_FORMAT
            )
            meeting_key = parsed_date.strftime(output_format)
            structured_raw_data["Date"] = meeting_key
            cc_meetings[meeting_key] = structured_raw_data
            r.hset(
                DETAIL_CC_MTG_KEY,
                mapping={meeting_key: json.dumps(structured_raw_data)},
            )
            r.expire(DETAIL_CC_MTG_KEY, DEFAULT_ONE_WEEK_SECONDS_EXPIRATION)
    return cc_meetings
