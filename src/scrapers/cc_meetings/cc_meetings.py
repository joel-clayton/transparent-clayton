import os
import re
import time
from datetime import datetime
from typing import TypedDict

from selenium import webdriver
from selenium.webdriver.common.by import By

from celery_app import app
from src.scrapers.cc_meetings.constants import (CLIP_ARG_REGEX, CLIP_ID_REGEX,
                                                DATE_INPUT_FORMAT,
                                                DOWNLOADED_PATH, SOURCE_URL,
                                                VIDEO_DATE_REGEX,
                                                VIDEO_FILE_NAME_REGEX)


def browser():
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
    Date: datetime
    Duration: str
    Agenda: str
    MinutesAndSupplementalMaterials: dict
    Video: str
    AgendaPacket: str


ordered_header_fields = [
    "Name",
    "Date",
    "Duration",
    "Agenda",
    "Minutes and Supplemental Materials",
    "Video",
    "Agenda Packet",
]


def parse_date_input(date_str):
    return datetime.strptime(date_str, DATE_INPUT_FORMAT)


def parse_url_from_html(html_str, start="//", end='">'):
    # if multiple matches, name the matches
    html_str = html_str.replace("&amp;", "&")
    matches = re.search(rf"{start}(.*?){end}", html_str)
    if matches:
        return "https://" + matches.group(1)
    return ""


def parse_multiple_urls_from_html(html_str, start=None, end=None):
    matches = re.findall(rf"{start}.*?{end}", html_str)
    named_urls = {}
    for match in matches:
        url_name = get_inner_text_from_html(match)
        url = parse_url_from_html(match)
        named_urls[url_name] = url
    return named_urls


def get_inner_text_from_html(html_str):
    matches = re.findall(r">(.*?)</", html_str)
    if matches:
        return matches[0]
    return ""


def get_clip_id_from_url(url):
    matches = re.search(CLIP_ARG_REGEX, url)
    if matches:
        return re.search(CLIP_ID_REGEX, matches.group(0)).group(0)


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


def get_latest_downloaded_date():
    filenames = os.listdir(DOWNLOADED_PATH)
    time_sorted_filenames = sorted(
        [filename for filename in filenames if filename.endswith(".mp4")], reverse=True
    )
    if not time_sorted_filenames:
        return None
    date_keyed_filenames = {}
    for filename in time_sorted_filenames:
        match = re.search(VIDEO_FILE_NAME_REGEX, filename)
        if match:
            date_match = re.search(VIDEO_DATE_REGEX, filename)
            date_keyed_filenames[date_match.group()] = filename
    date_sorted_filenames = sorted(date_keyed_filenames.keys(), reverse=True)
    if not date_sorted_filenames:
        return None
    return date_sorted_filenames[0]


def parse_meetings_from_url(latest_date):
    driver = browser()

    # Give the iframe time to load content
    time.sleep(2)
    driver.switch_to.frame(driver.find_element(By.TAG_NAME, "iframe"))
    parent_elem = driver.find_element(By.ID, "CollapsiblePanel2")
    cc_panel_elem = parent_elem.find_element(By.CLASS_NAME, "CollapsiblePanelContent")

    # Inspect and verify header fields
    table_heads = cc_panel_elem.find_elements(By.TAG_NAME, "thead")
    for index, table_head in enumerate(table_heads):
        header_cells = table_head.find_elements(By.XPATH, ".//th")
        header_cell_values = [cell.get_property("innerText") for cell in header_cells]
        if any(header_cell_values) and ordered_header_fields == header_cell_values:
            break

    # Inspect and parse table body fields
    cc_meetings = {}
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
                    body_cell_value = extra_parser(body_cell_value, **kwargs)
                structured_raw_data.update({header_cell_name: body_cell_value})
            structured_raw_data["ClipId"] = get_clip_id_from_url(
                structured_raw_data.get("Video", "")
            )
            cc_meetings[structured_raw_data["Date"].strftime("%Y-%m-%d")] = (
                CityCouncilMeeting(**structured_raw_data)
            )
    return cc_meetings


@app.task
def get_cc_meeting_details_for_download() -> dict:
    latest_date = get_latest_downloaded_date()
    parsed_meetings = parse_meetings_from_url(latest_date)
    meetings_to_process = sorted(
        [key for key in parsed_meetings.keys() if key > latest_date]
    )
    return dict(
        [
            (
                parsed_meetings[meeting_date]["Date"].strftime("%Y-%m-%d"),
                parsed_meetings[meeting_date]["ClipId"],
            )
            for meeting_date in meetings_to_process
        ]
    )
