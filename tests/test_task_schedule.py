import unittest

from task_schedule import TaskSchedule, TaskScheduleValidationError


class TaskScheduleTests(unittest.TestCase):
    def test_parses_a_valid_same_day_interval(self):
        schedule = TaskSchedule.from_mapping(
            {
                "start_date": "2026-07-18",
                "start_time": "09:00",
                "due_date": "2026-07-18",
                "due_time": "10:30",
            }
        )

        self.assertEqual(schedule.start_date, "2026-07-18")
        self.assertEqual(schedule.end_date, "2026-07-18")
        self.assertEqual(schedule.start_time, "09:00")
        self.assertEqual(schedule.end_time, "10:30")

    def test_rejects_backwards_dates_and_same_day_times(self):
        with self.assertRaisesRegex(TaskScheduleValidationError, "Start date"):
            TaskSchedule.from_mapping(
                {"start_date": "2026-07-19", "due_date": "2026-07-18"}
            )
        with self.assertRaisesRegex(TaskScheduleValidationError, "Start time"):
            TaskSchedule.from_mapping(
                {
                    "start_date": "2026-07-18",
                    "start_time": "11:00",
                    "due_date": "2026-07-18",
                    "due_time": "10:00",
                }
            )

    def test_allows_earlier_clock_time_on_a_later_end_date(self):
        schedule = TaskSchedule.from_mapping(
            {
                "start_date": "2026-07-18",
                "start_time": "18:00",
                "due_date": "2026-07-19",
                "due_time": "09:00",
            }
        )

        start, end = schedule.timed_bounds()
        self.assertLess(start, end)
        self.assertEqual(start.isoformat(), "2026-07-18T18:00:00")
        self.assertEqual(end.isoformat(), "2026-07-19T09:00:00")

    def test_end_time_without_start_time_keeps_legacy_duration(self):
        schedule = TaskSchedule.from_mapping(
            {"due_date": "2026-07-18", "due_time": "09:00"}
        )

        start, end = schedule.timed_bounds()
        self.assertEqual(start.isoformat(), "2026-07-18T09:00:00")
        self.assertEqual(end.isoformat(), "2026-07-18T09:30:00")

    def test_end_time_without_start_time_uses_the_end_date(self):
        schedule = TaskSchedule.from_mapping(
            {
                "start_date": "2026-07-18",
                "due_date": "2026-07-19",
                "due_time": "09:00",
            }
        )

        start, end = schedule.timed_bounds()
        self.assertEqual(start.isoformat(), "2026-07-19T09:00:00")
        self.assertEqual(end.isoformat(), "2026-07-19T09:30:00")

    def test_start_time_honours_a_later_end_date_without_an_end_time(self):
        schedule = TaskSchedule.from_mapping(
            {
                "start_date": "2026-07-18",
                "start_time": "09:00",
                "due_date": "2026-07-19",
            }
        )

        start, end = schedule.timed_bounds()
        self.assertEqual(start.isoformat(), "2026-07-18T09:00:00")
        self.assertEqual(end.isoformat(), "2026-07-19T09:00:00")

    def test_rejects_non_canonical_iso_dates(self):
        with self.assertRaisesRegex(TaskScheduleValidationError, "YYYY-MM-DD"):
            TaskSchedule.from_mapping({"start_date": "20260718"})


if __name__ == "__main__":
    unittest.main()
