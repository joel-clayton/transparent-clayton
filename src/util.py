import json
from typing import Any, List

from celery_app import r
from src.constants import SCRAPED_CC_MTG_KEY


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
