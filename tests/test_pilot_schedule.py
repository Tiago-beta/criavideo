from datetime import date

from app.services.pilot_schedule import (
    PILOT_LONG_SCHEDULE_SUMMARY,
    PILOT_SHORT_SCHEDULE_SUMMARY,
    PILOT_TOTAL_SHORTS_PER_CYCLE,
    build_pilot_short_publish_plan,
    resolve_publish_datetime_utc,
)


def test_pilot_short_publish_plan_creates_two_days_of_five_slots():
    plan = build_pilot_short_publish_plan(date(2026, 4, 27))

    assert len(plan) == PILOT_TOTAL_SHORTS_PER_CYCLE
    assert [item["release_date_iso"] for item in plan[:5]] == ["2026-04-28"] * 5
    assert [item["time_local"] for item in plan[:5]] == ["18:00", "19:00", "20:00", "21:00", "22:00"]
    assert [item["release_date_iso"] for item in plan[5:]] == ["2026-04-29"] * 5
    assert [item["time_local"] for item in plan[5:]] == ["18:00", "19:00", "20:00", "21:00", "22:00"]


def test_resolve_publish_datetime_utc_converts_sao_paulo_slots():
    publish_at = resolve_publish_datetime_utc(
        release_date=date(2026, 4, 28),
        publish_time_local="21:00",
        timezone="America/Sao_Paulo",
    )

    assert publish_at.isoformat() == "2026-04-29T00:00:00"


def test_pilot_schedule_summaries_are_human_readable():
    assert PILOT_LONG_SCHEDULE_SUMMARY == "Seg e Qui as 20:00"
    assert PILOT_SHORT_SCHEDULE_SUMMARY == "Ter, Qua, Sex e Sab as 18:00, 19:00, 20:00, 21:00 e 22:00"