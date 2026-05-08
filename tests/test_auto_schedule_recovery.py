from datetime import datetime
import unittest

import app.scheduler as scheduler_module
from app.models import AutoSchedule, AutoScheduleTheme


class TestAutoScheduleRecovery(unittest.TestCase):
    def test_auto_schedule_recovery_uses_local_day_for_manual_due_theme_after_utc_midnight(self):
        schedule = AutoSchedule(
            id=44,
            name="Piloto automatico - Olevita - Shorts",
            frequency="daily",
            time_utc="21:00",
            day_of_week=1,
            timezone="America/Sao_Paulo",
            default_settings={"active_weekdays": [1, 2, 4, 5]},
            video_type="musical_shorts",
            is_active=True,
        )
        pending_theme = AutoScheduleTheme(
            auto_schedule_id=44,
            theme="Mensagem para seu coração — Trecho 6",
            status="pending",
            custom_settings={"scheduled_date_override": "2026-05-07"},
        )

        should_trigger, _reason = scheduler_module._should_trigger_auto_schedule(
            schedule,
            [pending_theme],
            datetime(2026, 5, 8, 0, 15),
            allow_missed_window=True,
        )

        self.assertTrue(should_trigger)

    def test_auto_schedule_recovery_catches_up_same_local_day_after_restart(self):
        schedule = AutoSchedule(
            id=43,
            name="Piloto automatico - Olevita - Long",
            frequency="daily",
            time_utc="23:00",
            day_of_week=0,
            timezone="America/Sao_Paulo",
            default_settings={"active_weekdays": [0, 3]},
            video_type="music",
            is_active=True,
        )
        pending_theme = AutoScheduleTheme(
            auto_schedule_id=43,
            theme="Louvor curto e poderoso",
            status="pending",
            custom_settings={},
        )

        should_trigger, reason = scheduler_module._should_trigger_auto_schedule(
            schedule,
            [pending_theme],
            datetime(2026, 5, 7, 23, 22),
            allow_missed_window=True,
        )

        self.assertTrue(should_trigger)
        self.assertEqual(reason, "recovering missed auto-schedule run after scheduler startup")


if __name__ == "__main__":
    unittest.main()