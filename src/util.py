import json
import os
import re
from os import listdir, path
from typing import Any

from celery_app import r
from src.constants import SCRAPED_CC_MTG_KEY
from src.types import SourceType, type_stubs


def get_detail_from_redis(key) -> Any:
    serialized = r.get(key)
    if serialized:
        return json.loads(serialized)
    return None


def get_dates_to_process() -> [str]:
    dates_to_upload = r.get(SCRAPED_CC_MTG_KEY)
    if dates_to_upload:
        return json.loads(dates_to_upload)
    return ['']


def get_most_recent_missing_dates(input_dir: str, output_dir: str, source_type: SourceType) -> list:
    """
    Compare two sets of dates from file names and find the list of src
     directory dates not represented in the destination directory
    """
    src_dates = sorted(gather_dates(input_dir, source_type))
    dst_dates = sorted(gather_dates(output_dir, source_type))

    return sorted(list(set(src_dates) - set(dst_dates)))


def gather_dates(dir_path: str, source_type: SourceType) -> list:
    """
    Get all dates from file names in a specified directory, optionally
     using a pattern to filter file names down to a particular set
    """
    file_name_stub = type_stubs.get(source_type)
    files = [
        f
        for f in listdir(dir_path)
        if path.isfile(os.path.join(dir_path, f))
        if file_name_stub in f
    ]
    dates = []
    for f in files:
        date = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", f)
        if date:
            dates.append(date.group())
    return sorted(dates)
