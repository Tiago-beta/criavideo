from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PILOT_TIMEZONE = "America/Sao_Paulo"

PILOT_LONG_WEEKDAYS = (0, 3)
PILOT_LONG_PUBLISH_TIME_LOCAL = "20:00"
PILOT_LONG_TIME_UTC = "23:00"
PILOT_LONG_SCHEDULE_SUMMARY = "Seg e Qui as 20:00"

PILOT_SHORT_WEEKDAYS = (1, 2, 4, 5)
PILOT_SHORT_RUN_TIME_LOCAL = "18:00"
PILOT_SHORT_RUN_TIME_UTC = "21:00"
PILOT_SHORT_PUBLISH_SLOTS_LOCAL = ("18:00", "19:00", "20:00", "21:00", "22:00")
PILOT_SHORT_DAYS_AFTER_LONG = (1, 2)
PILOT_SHORTS_PER_DAY = len(PILOT_SHORT_PUBLISH_SLOTS_LOCAL)
PILOT_TOTAL_SHORTS_PER_CYCLE = PILOT_SHORTS_PER_DAY * len(PILOT_SHORT_DAYS_AFTER_LONG)
PILOT_SHORT_SCHEDULE_SUMMARY = "Ter, Qua, Sex e Sab as 18:00, 19:00, 20:00, 21:00 e 22:00"


def build_pilot_short_publish_plan(long_release_date: date) -> list[dict]:
    plan: list[dict] = []
    slot_index = 0

    for day_offset in PILOT_SHORT_DAYS_AFTER_LONG:
        release_date = long_release_date + timedelta(days=day_offset)
        for time_local in PILOT_SHORT_PUBLISH_SLOTS_LOCAL:
            plan.append(
                {
                    "release_date": release_date,
                    "release_date_iso": release_date.isoformat(),
                    "time_local": time_local,
                    "slot_index": slot_index,
                }
            )
            slot_index += 1

    return plan


def resolve_publish_datetime_utc(
    release_date: date,
    publish_time_local: str,
    timezone: str = PILOT_TIMEZONE,
) -> datetime:
    raw_time = str(publish_time_local or "").strip()
    if not raw_time:
        raise ValueError("publish_time_local is required")

    hour, minute = map(int, raw_time.split(":", 1))
    tz = ZoneInfo(timezone or PILOT_TIMEZONE)
    local_dt = datetime(
        release_date.year,
        release_date.month,
        release_date.day,
        hour,
        minute,
        tzinfo=tz,
    )
    return local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)