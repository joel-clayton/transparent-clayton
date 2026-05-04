import json
import re
from typing import Any, List

from celery_app import r
from src.constants import (
    SCRAPED_CC_MTG_KEY,
    DATE_PATTERN,
    YEAR_PATTERN,
    DATETIME_PATTERN,
    DATETIME_OUTPUT_PATTERN,
)


def get_detail_from_redis(key: str) -> Any:
    serialized = r.get(key)
    if serialized:
        return json.loads(serialized)
    return None


def get_dates_to_process() -> List[str]:
    dates_to_upload = r.get(SCRAPED_CC_MTG_KEY)
    if dates_to_upload:
        return json.loads(dates_to_upload)
    return [""]


def get_date_string_from_string(text: str) -> str:
    date_match = re.search(DATE_PATTERN, text)
    if date_match:
        return date_match.group(0)
    return ""


def get_datetime_string_from_string(text: str) -> str:
    datetime_match = re.search(DATETIME_PATTERN, text)
    if datetime_match:
        return datetime_match.group(0)
    # todo convert this to a common comparable format for both input and output
    datetime_output_match = re.search(DATETIME_OUTPUT_PATTERN, text)
    if datetime_output_match:
        return datetime_output_match.group(0).replace(":", "_")
    return ""


def get_year_string_from_string(text: str) -> str:
    year_match = re.search(YEAR_PATTERN, text)
    if year_match:
        return year_match.group(0)
    return ""
