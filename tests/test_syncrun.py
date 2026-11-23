import unittest

from ttgtsync import config
from ttgtsync.store import Store
from ttgtsync.syncrun import Syncer, pick_masters


class FakeSecrets:
    def access_token(self, app, force_refresh=False):
        return "test-token"

    def refresh_age_days(self, app):
        return None

    def needs_reauth(self, app, warn_days=21.0):
        return False

    def access_expiry_days(self, app):
        return None


class FakeTasks:
    def __init__(self):
        self.deleted = []
        self.completed = []

    def delete_task(self, list_id, task_id):
        self.deleted.append((list_id, task_id))

    def patch_task(self, list_id, task_id, body, update_mask=None):
        self.completed.append((list_id, task_id, body.get("status")))
        return {"id": task_id, "status": body.get("status")}


class FakeCalendar:
    def __init__(self):
        self.deleted = []

    def delete_event(self, calendar_id, event_id):
        self.deleted.append(event_id)


class SyncTestBase(unittest.TestCase):
    def build(self, overrides=None):
        cfg = config.load(None)
        cfg["google"]["calendar"]["id"] = "cal"
        if overrides:
            for key, value in overrides.items():
                cfg[key] = value
        store = Store(":memory:")
        self.addCleanup(store.close)
        syncer = Syncer(cfg, FakeSecrets(), store)
        syncer.gt = FakeTasks()
        syncer.gcal = FakeCalendar()
        return syncer, store


def empty_stats():
    return {key: 0 for key in (
        "pairs", "created_gt", "created_tt", "pushed_down", "pushed_up",
        "created_event", "updated_event", "deleted_gt", "deleted_tt",
        "deleted_event", "migrated", "adopted", "conflicts", "skipped", "errors",
        "completed_gt",
    )}


class SweepTests(SyncTestBase):
    def test_requires_two_consecutive_misses(self):
        syncer, store = self.build()
        store.save_task({
            "tt_id": "t1", "domain": "tasks", "gt_id": "g1", "gt_list_id": "l1", "tombstone": 0,
        })
        syncer.tt_tasks = {}
        stats = empty_stats()

        syncer._sweep(stats)
        self.assertEqual(syncer.gt.completed, [])
        self.assertEqual(store.get_meta("missing:t1"), "1")

        syncer._sweep(stats)
        self.assertEqual(syncer.gt.completed, [("l1", "g1", "completed")])
        self.assertEqual(stats["completed_gt"], 1)

    def test_disappear_delete_mode(self):
        syncer, store = self.build({"on_disappear": "delete"})
        store.save_task({
            "tt_id": "t1", "domain": "tasks", "gt_id": "g1", "gt_list_id": "l1", "tombstone": 0,
        })
        syncer.tt_tasks = {}
        stats = empty_stats()
        syncer._sweep(stats)
        syncer._sweep(stats)
        self.assertEqual(syncer.gt.deleted, [("l1", "g1")])
        self.assertEqual(stats["deleted_gt"], 1)

    def test_present_task_resets_strikes(self):
        syncer, store = self.build()
        store.save_task({
            "tt_id": "t1", "domain": "tasks", "gt_id": "g1", "gt_list_id": "l1", "tombstone": 0,
        })
        store.set_meta("missing:t1", "1")
        syncer.tt_tasks = {"p1": [{"id": "t1"}]}

        syncer._sweep(empty_stats())
        self.assertEqual(store.get_meta("missing:t1"), "0")
        self.assertEqual(syncer.gt.deleted, [])

    def test_calendar_domain_sweeps_event(self):
        syncer, store = self.build()
        store.save_task({
            "tt_id": "t2", "domain": "calendar", "gt_id": "e1", "gt_calendar_id": "cal",
            "tombstone": 0,
        })
        syncer.tt_tasks = {}
        stats = empty_stats()
        syncer._sweep(stats)
        syncer._sweep(stats)
        self.assertEqual(syncer.gcal.deleted, ["e1"])
        self.assertEqual(stats["deleted_event"], 1)


class TombstoneTests(SyncTestBase):
    def test_unchanged_tombstoned_task_stays_dead(self):
        syncer, store = self.build()
        snapshot = {"title": "x", "notes": "", "due": None, "status": "needsAction"}
        store.save_task({
            "tt_id": "t1", "domain": "calendar", "tombstone": 1,
            "tombstone_at": "2026-11-24T00:00:00Z", "tt_snapshot": snapshot,
        })
        stats = empty_stats()
        row = store.task("t1")
        row, skip = syncer._revive_if_edited(row, snapshot, stats)
        self.assertTrue(skip)
        self.assertEqual(store.task("t1")["tombstone"], 1)

    def test_edited_tombstoned_task_revives(self):
        syncer, store = self.build()
        snapshot = {"title": "x", "notes": "", "due": None, "status": "needsAction"}
        store.save_task({
            "tt_id": "t1", "domain": "calendar", "tombstone": 1,
            "tombstone_at": "2026-11-24T00:00:00Z", "tt_snapshot": snapshot,
        })
        stats = empty_stats()
        row = store.task("t1")
        changed = dict(snapshot, title="re-added")
        _, skip = syncer._revive_if_edited(row, changed, stats)
        self.assertFalse(skip)
        self.assertEqual(store.task("t1")["tombstone"], 0)


class PairingTests(SyncTestBase):
    def test_inbox_is_included(self):
        syncer, _ = self.build()
        syncer.projects = {
            "inbox": {"id": "inbox", "name": "Inbox"},
            "p1": {"id": "p1", "name": "Calendar"},
        }
        wanted = syncer._wanted_projects()
        self.assertEqual(sorted(wanted), ["inbox", "p1"])

    def test_exclude_wins(self):
        syncer, _ = self.build({"lists": {"include": [], "exclude": ["Routine"]}})
        syncer.projects = {
            "p1": {"id": "p1", "name": "Calendar"},
            "p2": {"id": "p2", "name": "Routine"},
        }
        wanted = syncer._wanted_projects()
        self.assertEqual(list(wanted), ["p1"])


class MasterSelectionTests(unittest.TestCase):
    def test_occurrence_exceptions_are_ignored(self):
        master = {
            "id": "m1", "status": "confirmed", "recurrence": ["RRULE:FREQ=DAILY"],
            "extendedProperties": {"private": {"tt_id": "t1"}},
        }
        exception = {
            "id": "m1_20261124T150000Z", "recurringEventId": "m1", "status": "confirmed",
            "extendedProperties": {"private": {"tt_id": "t1"}},
        }
        chosen = pick_masters([exception, master])
        self.assertEqual(chosen["t1"]["id"], "m1")

    def test_unmarked_events_are_dropped(self):
        self.assertEqual(pick_masters([{"id": "x", "summary": "meeting"}]), {})


if __name__ == "__main__":
    unittest.main()
