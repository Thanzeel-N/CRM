"""Store UTC instants; apply the organization's region at display/query boundaries."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = 'Asia/Kolkata'


def utc_naive(value):
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc).replace(tzinfo=None)


def local_datetime(value, region=DEFAULT_TIMEZONE):
    if value is None:
        return None
    return utc_naive(value).replace(tzinfo=timezone.utc).astimezone(ZoneInfo(region))


def day_bounds(day, region=DEFAULT_TIMEZONE):
    zone = ZoneInfo(region)
    return (utc_naive(datetime.combine(day, time.min, zone)),
            utc_naive(datetime.combine(day + timedelta(days=1), time.min, zone)))
