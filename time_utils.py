import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def app_now():
    timezone_name = os.environ.get("PING_MONITOR_TZ", "").strip()
    if timezone_name:
        try:
            return datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)
        except ZoneInfoNotFoundError:
            pass

    return datetime.now().astimezone().replace(tzinfo=None)


def minute_floor(value):
    return value.replace(second=0, microsecond=0)
