import unittest

from ttgtsync import classify


def task(**overrides):
    base = {"id": "t1", "title": "x", "dueDate": "2026-11-24T00:00:00+0100", "isAllDay": True}
    base.update(overrides)
    return base


class ClassifyTests(unittest.TestCase):
    def test_all_day_is_tasks_domain(self):
        self.assertFalse(classify.is_timed(task()))
        self.assertEqual(classify.domain_of(task()), classify.TASKS)

    def test_timed_is_calendar_domain(self):
        timed = task(dueDate="2026-11-24T17:30:00+0100", isAllDay=False)
        self.assertTrue(classify.is_timed(timed))
        self.assertEqual(classify.domain_of(timed), classify.CALENDAR)

    def test_missing_is_all_day_uses_time_of_day(self):
        self.assertTrue(classify.is_timed({"dueDate": "2026-11-24T17:30:00+0100"}))
        self.assertFalse(classify.is_timed({"dueDate": "2026-11-24T00:00:00+0100"}))

    def test_undated(self):
        self.assertIsNone(classify.domain_of({"id": "x", "title": "no date"}))
        self.assertEqual(
            classify.domain_of({"id": "x", "title": "no date"}, include_undated=True),
            classify.TASKS,
        )

    def test_skip_rules(self):
        self.assertTrue(classify.should_skip(task(deleted=True)))
        self.assertTrue(classify.should_skip(task(repeatFlag="RRULE:FREQ=DAILY")))
        self.assertFalse(classify.should_skip(task(repeatFlag="RRULE:FREQ=DAILY"), skip_recurring=False))
        self.assertFalse(classify.should_skip(task()))

    def test_completed(self):
        self.assertTrue(classify.is_completed(task(status=2)))
        self.assertFalse(classify.is_completed(task(status=0)))


if __name__ == "__main__":
    unittest.main()
