import json
import re
from datetime import datetime
from typing import Any, List

from celery_app import r
from src.constants import (
    SCRAPED_CC_MTG_KEY,
    DATE_PATTERN,
    YEAR_PATTERN,
    DATETIME_PATTERN,
    DATETIME_OUTPUT_PATTERN,
    DATETIME_FORMAT,
    DATE_FORMAT,
)
from src.processors.constants import (
    FORMAL_DATE_PATTERN,
    FORMAL_DATETIME_PATTERN,
    NATURAL_DATE_FORMAT,
    NATURAL_DATETIME_FORMAT,
    TIME_PATTERN,
)
from src.secrets import DISCORD_WEBHOOK_BOTS


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
    datetime_output_match = re.search(DATETIME_OUTPUT_PATTERN, text)
    if datetime_output_match:
        return datetime_output_match.group(0).replace(":", "_")
    return ""


def get_natural_date_string_from_string(text: str) -> str:
    formal_date_match = re.search(FORMAL_DATE_PATTERN, text)
    if formal_date_match:
        return formal_date_match.group(0)
    return ""


def get_natural_datetime_string_from_string(text: str) -> str:
    formal_datetime_match = re.search(FORMAL_DATETIME_PATTERN, text)
    if formal_datetime_match:
        return formal_datetime_match.group(0)
    return ""


def get_date_or_datetime_string_from_string(text: str) -> str:
    has_time_data = bool(re.search(TIME_PATTERN, text))
    dt = get_datetime_from_string(text)
    if dt is None:
        return ""
    if has_time_data:
        return dt.strftime(DATETIME_FORMAT)
    else:
        return dt.strftime(DATE_FORMAT)


def get_datetime_from_string(text: str) -> datetime | None:
    datetime_string = get_datetime_string_from_string(text)
    if datetime_string:
        return datetime.strptime(datetime_string, DATETIME_FORMAT)
    date_string = get_date_string_from_string(text)
    if date_string:
        return datetime.strptime(date_string, DATE_FORMAT)
    natural_datetime_string = get_natural_datetime_string_from_string(text)
    if natural_datetime_string:
        return datetime.strptime(natural_datetime_string, NATURAL_DATETIME_FORMAT)
    natural_date_string = get_natural_date_string_from_string(text)
    if natural_date_string:
        return datetime.strptime(natural_date_string, NATURAL_DATE_FORMAT)
    return None


def convert_natural_datetime_string_to_datetime_string(text: str) -> str:
    dt = datetime.strptime(text, NATURAL_DATETIME_FORMAT)
    return dt.strftime(DATETIME_FORMAT)


def get_year_string_from_string(text: str) -> str:
    year_match = re.search(YEAR_PATTERN, text)
    if year_match:
        return year_match.group(0)
    return ""


def send_to_discord_bots(message: str) -> None:
    from discord import SyncWebhook

    webhook = SyncWebhook.from_url(DISCORD_WEBHOOK_BOTS)
    webhook.send(message)


def get_part_num_from_string(string: str) -> int:
    part_match = re.search(r"part [0-9]+", string)
    if part_match:
        num_only = re.search("[0-9]+", part_match.group(0))
        if num_only:
            return int(num_only.group(0))
    part_match = re.search(r"\b\d{3}\b", string)
    if part_match:
        return int(part_match.group(0)) + 1
    return 1
