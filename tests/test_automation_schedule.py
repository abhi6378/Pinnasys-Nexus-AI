import unittest
from datetime import UTC, datetime

from automation import schedule
from automation.keys import build_run_idempotency_key, build_run_key


class AutomationScheduleTests(unittest.TestCase):
    def test_once_schedule_uses_timezone_and_returns_utc(self):
        spec = {
            "schedule_type": "once",
            "timezone": "Asia/Calcutta",
            "start_at": "2026-04-23T09:00:00",
        }
        next_run = schedule.compute_next_run_at(
            spec,
            after=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        )
        self.assertEqual(next_run.tzinfo, UTC)
        self.assertEqual(next_run.hour, 3)
        self.assertEqual(next_run.minute, 30)

    def test_interval_schedule_advances_from_previous_run(self):
        spec = {
            "schedule_type": "interval",
            "timezone": "UTC",
            "start_at": "2026-04-22T10:00:00+00:00",
            "interval_seconds": 3600,
        }
        next_run = schedule.compute_next_run_at(
            spec,
            after=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
            previous_run_at=datetime(2026, 4, 22, 11, 0, tzinfo=UTC),
        )
        self.assertEqual(next_run, datetime(2026, 4, 22, 12, 0, tzinfo=UTC))

    def test_cron_validation_is_explicit_when_dependency_missing(self):
        errors = schedule.validate_schedule_spec(
            {"schedule_type": "cron", "timezone": "UTC", "cron_expression": "0 9 * * *"}
        )
        if schedule.croniter is None:
            self.assertIn("croniter", " ".join(errors))
        else:
            self.assertEqual(errors, [])

    def test_run_keys_are_stable_and_planned_time_scoped(self):
        planned = datetime(2026, 4, 22, 9, 0, tzinfo=UTC)
        key_a = build_run_key("task_1", planned, "workflow", "email_triage")
        key_b = build_run_key("task_1", planned, "workflow", "email_triage")
        key_c = build_run_key("task_1", planned.replace(hour=10), "workflow", "email_triage")
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

        idem_a = build_run_idempotency_key("task_1", planned, {"message": "hello"})
        idem_b = build_run_idempotency_key("task_1", planned, {"message": "hello"})
        self.assertEqual(idem_a, idem_b)


if __name__ == "__main__":
    unittest.main()
