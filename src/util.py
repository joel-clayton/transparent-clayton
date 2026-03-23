import json
from typing import Any

from celery_app import r


def get_detail_from_redis(key) -> Any:
    serialized = r.get(key)
    if serialized:
        return json.loads(serialized)
    return None
