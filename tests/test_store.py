import unittest

from ttgtsync.store import Store, load_snapshot


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_pairs_roundtrip(self):
        self.store.upsert_pair("Calendar", "p1", "l1")
        pairs = self.store.pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(self.store.pair_by_tt("p1")["gt_list_id"], "l1")
        self.assertEqual(self.store.pair_by_gt("l1")["tt_project_id"], "p1")

    def test_task_roundtrip_with_snapshots(self):
        self.store.save_task({
            "tt_id": "t1", "domain": "tasks", "tt_project_id": "p1",
            "gt_id": "g1", "gt_list_id": "l1",
            "tt_snapshot": {"title": "x", "notes": "a\nb"},
            "gt_snapshot": {"title": "x", "notes": "a\nb"},
            "tombstone": 0,
        })
        row = self.store.task("t1")
        self.assertEqual(row["gt_id"], "g1")
        self.assertEqual(load_snapshot(row["tt_snapshot"]), {"title": "x", "notes": "a\nb"})
        self.assertEqual(self.store.task_by_gt("g1")["tt_id"], "t1")

    def test_clear_gt_link_tombstones(self):
        self.store.save_task({
            "tt_id": "t1", "domain": "calendar", "gt_id": "e1",
            "gt_list_id": None, "gt_calendar_id": "c1", "tombstone": 0,
        })
        self.store.clear_gt_link("t1", "event_deleted")
        row = self.store.task("t1")
        self.assertIsNone(row["gt_id"])
        self.assertEqual(row["tombstone"], 1)
        self.assertEqual(row["tombstone_reason"], "event_deleted")

    def test_linked_tasks_excludes_tombstones(self):
        self.store.save_task({"tt_id": "t1", "domain": "tasks", "gt_id": "g1", "tombstone": 0})
        self.store.save_task({"tt_id": "t2", "domain": "tasks", "gt_id": "g2", "tombstone": 1})
        linked = [row["tt_id"] for row in self.store.linked_tasks()]
        self.assertEqual(linked, ["t1"])

    def test_purge_old_tombstones(self):
        self.store.save_task({
            "tt_id": "old", "domain": "tasks", "tombstone": 1,
            "tombstone_at": "2020-01-01T00:00:00Z",
        })
        self.store.save_task({
            "tt_id": "new", "domain": "tasks", "tombstone": 1,
            "tombstone_at": "2099-01-01T00:00:00Z",
        })
        self.store.purge_tombstones(30)
        self.assertIsNone(self.store.task("old"))
        self.assertIsNotNone(self.store.task("new"))

    def test_runs(self):
        run_id = self.store.start_run()
        self.store.finish_run(run_id, "ok", {"created_gt": 2})
        latest = self.store.last_runs(1)[0]
        self.assertEqual(latest["status"], "ok")
        self.assertIn("created_gt", latest["stats"])


if __name__ == "__main__":
    unittest.main()
