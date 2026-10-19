"""Named-zone calendar recurrence, independent of Discord and the host timezone."""

from datetime import datetime, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name):
    if type(name) is not str or len(name) > 100:
        raise ValueError("Choose a named timezone.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown timezone. Install the pinned timezone data and choose a valid IANA name.") from None


def calendar_policy(value=None):
    value = value or {}
    if type(value) is not dict or not value.keys() <= {"timezone","recurrence","weekdays","local_time"}:
        raise ValueError("Invalid calendar recurrence.")
    result = {"timezone":"UTC","recurrence":"interval","weekdays":[],"local_time":"09:00"} | value
    zone(result["timezone"])
    if result["recurrence"] not in ("once","interval","daily","weekly"):
        raise ValueError("Choose once, interval, daily, or weekly recurrence.")
    if type(result["local_time"]) is not str or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",result["local_time"]):
        raise ValueError("Use a local time such as 18:00.")
    days = result["weekdays"]
    if type(days) is not list or len(days) > 7 or any(type(day) is not int or not 0 <= day <= 6 for day in days) or len(set(days)) != len(days):
        raise ValueError("Weekdays must be unique numbers from Monday (0) to Sunday (6).")
    if result["recurrence"] == "weekly" and not days:
        raise ValueError("Choose at least one weekday.")
    return result


def local_timestamp(value, timezone_name):
    try:
        local = datetime.fromisoformat(value)
    except (ValueError,TypeError):
        raise ValueError("Choose a local date and time.") from None
    if local.tzinfo is not None:
        raise ValueError("Use a local date/time and choose its timezone separately.")
    aware = local.replace(tzinfo=zone(timezone_name),fold=0)
    if datetime.fromtimestamp(aware.timestamp(),aware.tzinfo).replace(tzinfo=None) != local:
        raise ValueError("That time is skipped by a daylight-saving clock change. Choose another time.")
    return aware.timestamp()


def next_occurrence(after, policy):
    policy = calendar_policy(policy)
    tz = zone(policy["timezone"])
    start = datetime.fromtimestamp(after,tz)
    hour,minute = map(int,policy["local_time"].split(":"))
    for offset in range(9):
        day = start.date()+timedelta(days=offset)
        if policy["recurrence"] == "weekly" and day.weekday() not in policy["weekdays"]:
            continue
        naive = datetime.combine(day,datetime.min.time()).replace(hour=hour,minute=minute)
        candidate = naive.replace(tzinfo=tz,fold=0)
        # A skipped wall time is omitted; an ambiguous time uses its first occurrence once.
        if datetime.fromtimestamp(candidate.timestamp(),tz).replace(tzinfo=None) != naive:
            continue
        if candidate.timestamp() > after:
            return candidate.timestamp()
    raise ValueError("No calendar occurrence could be found.")


def policy_from_row(row):
    return {"timezone":row.get("timezone","UTC"),"recurrence":row.get("recurrence","interval"),
            "weekdays":json.loads(row.get("weekdays","[]")),"local_time":row.get("local_time","09:00")}


def following_due(row, after):
    policy = policy_from_row(row)
    if policy["recurrence"] in ("daily","weekly"):
        return next_occurrence(after,policy)
    if policy["recurrence"] != "once" and row["repeat_days"]:
        interval = row["repeat_days"]*86400
        return row["due_at"]+(int(max(0,after-row["due_at"])//interval)+1)*interval
    return None
