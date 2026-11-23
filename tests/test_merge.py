import unittest

from ttgtsync import merge


class MergeTests(unittest.TestCase):
    def test_changed(self):
        self.assertTrue(merge.changed(None, {"a": 1}))
        self.assertFalse(merge.changed({"a": 1}, {"a": 1}))
        self.assertTrue(merge.changed({"a": 1}, {"a": 2}))

    def test_single_side_changes(self):
        self.assertEqual(merge.resolve(True, False), (merge.PUSH_DOWN, False))
        self.assertEqual(merge.resolve(False, True), (merge.PUSH_UP, False))
        self.assertEqual(merge.resolve(False, False), (merge.NONE, False))

    def test_conflict_policies(self):
        self.assertEqual(merge.resolve(True, True, "tt"), (merge.PUSH_DOWN, True))
        self.assertEqual(merge.resolve(True, True, "gt"), (merge.PUSH_UP, True))

    def test_newest_wins(self):
        action, conflict = merge.resolve(
            True, True, "newest",
            tt_stamp="2026-11-24T12:00:00+0100",
            gt_stamp="2026-11-24T10:00:00.000Z",
        )
        self.assertEqual(action, merge.PUSH_DOWN)
        self.assertTrue(conflict)

        action, _ = merge.resolve(
            True, True, "newest",
            tt_stamp="2026-11-24T08:00:00+0100",
            gt_stamp="2026-11-24T10:00:00.000Z",
        )
        self.assertEqual(action, merge.PUSH_UP)

    def test_domain_migration(self):
        self.assertTrue(merge.out_of_domain("calendar", "tasks"))
        self.assertFalse(merge.out_of_domain("tasks", "tasks"))
        self.assertFalse(merge.out_of_domain(None, "tasks"))


if __name__ == "__main__":
    unittest.main()
