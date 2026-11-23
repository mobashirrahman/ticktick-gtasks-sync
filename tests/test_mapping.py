import unittest

from ttgtsync import mapping
from ttgtsync.duration import find_minutes


UNTIMED = {
    "id": "abc123",
    "projectId": "p1",
    "title": "Dentist",
    "content": "Bring card",
    "priority": 5,
    "status": 0,
    "tags": ["#routine"],
    "isAllDay": True,
    "dueDate": "2026-11-24T00:00:00+0100",
    "timeZone": "Europe/Berlin",
}

TIMED = {
    "id": "t9",
    "projectId": "p1",
    "title": "Deep work",
    "content": "Write the plan",
    "priority": 0,
    "status": 0,
    "tags": ["#15min"],
    "isAllDay": False,
    "dueDate": "2026-11-24T17:30:00+0100",
    "timeZone": "Europe/Berlin",
}


class TasksDomainTests(unittest.TestCase):
    def test_payload_carries_marker_and_due_date(self):
        payload = mapping.gt_task_payload(UNTIMED)
        self.assertEqual(payload["title"], "Dentist")
        self.assertEqual(payload["due"], "2026-11-24T00:00:00.000Z")
        self.assertIn("[tt:abc123]", payload["notes"])

    def test_round_trip_is_stable(self):
        payload = mapping.gt_task_payload(UNTIMED)
        echo = {
            "id": "g1",
            "title": payload["title"],
            "notes": payload["notes"],
            "due": payload["due"],
            "status": payload["status"],
            "updated": "2026-11-24T10:00:00.000Z",
        }
        self.assertEqual(mapping.gt_task_view(echo), mapping.tt_task_view(UNTIMED))

    def test_encode_is_idempotent_after_decode(self):
        payload = mapping.gt_task_payload(UNTIMED)
        body = mapping.tt_from_gt({"title": payload["title"], "notes": payload["notes"],
                                   "due": payload["due"], "status": "needsAction"}, "p1")
        body["id"] = "abc123"
        self.assertEqual(
            mapping.tt_task_view(body)["notes"],
            mapping.tt_task_view(UNTIMED)["notes"],
        )

    def test_from_gt_sets_all_day_and_date(self):
        body = mapping.tt_from_gt(
            {"title": "Buy milk", "notes": "2 liters", "due": "2026-11-25T00:00:00.000Z",
             "status": "needsAction"},
            "p1",
        )
        self.assertEqual(body["title"], "Buy milk")
        self.assertTrue(body["isAllDay"])
        self.assertTrue(body["dueDate"].startswith("2026-11-25T00:00:00"))
        self.assertEqual(body["content"], "2 liters")

    def test_completed_status_maps_both_ways(self):
        done = dict(UNTIMED, status=2)
        self.assertEqual(mapping.gt_task_payload(done)["status"], "completed")
        body = mapping.tt_from_gt({"title": "x", "status": "completed"}, "p1")
        self.assertEqual(body["status"], 2)

    def test_marker_helpers(self):
        notes = mapping.with_marker("hello", "id1")
        self.assertTrue(notes.endswith("[tt:id1]"))
        self.assertEqual(mapping.marker_id(notes), "id1")
        self.assertEqual(mapping.strip_marker(notes), "hello")
        self.assertIsNone(mapping.marker_id("plain note"))

    def test_event_marker_never_leaks_into_content(self):
        description = "[ticktick:t9]\n[start:2026-11-24T16:00:00.000+0000]"
        decoded = mapping.decode_notes(description)
        self.assertNotIn("ticktick", decoded["content"])
        self.assertEqual(decoded["startDate"], "2026-11-24T16:00:00.000+0000")

    def test_encode_drops_marker_from_content(self):
        task = dict(UNTIMED, content="Bring card\n\n[tt:abc123]")
        self.assertNotIn("[tt:abc123]", mapping.encode_notes(task))

    def test_strip_event_marker_anywhere(self):
        self.assertEqual(mapping.strip_event_marker("[ticktick:x]\nbody"), "body")
        self.assertEqual(mapping.strip_event_marker("body\n\n[ticktick:x]"), "body")


class CalendarDomainTests(unittest.TestCase):
    def test_duration_from_tag_drives_event_end(self):
        minutes = find_minutes(TIMED["tags"])
        payload = mapping.event_payload(TIMED, minutes)
        self.assertEqual(minutes, 15)
        self.assertEqual(payload["start"]["dateTime"], "2026-11-24T17:30:00+01:00")
        self.assertEqual(payload["end"]["dateTime"], "2026-11-24T17:45:00+01:00")
        self.assertEqual(payload["extendedProperties"]["private"]["tt_id"], "t9")

    def test_default_duration_when_no_tag(self):
        minutes = find_minutes([], default=30)
        payload = mapping.event_payload(TIMED, minutes)
        self.assertEqual(payload["end"]["dateTime"], "2026-11-24T18:00:00+01:00")

    def test_event_echo_matches_desired_projection(self):
        minutes = find_minutes(TIMED["tags"])
        payload = mapping.event_payload(TIMED, minutes)
        echo = {
            "summary": payload["summary"],
            "start": payload["start"],
            "end": payload["end"],
            "description": payload["description"],
            "status": "confirmed",
            "updated": "2026-11-24T10:00:00.000Z",
        }
        view = mapping.event_view(echo)
        view.pop("status")
        self.assertEqual(view, mapping.desired_event_view(TIMED, minutes))

    def test_resize_maps_back_to_ticktick(self):
        event = {
            "summary": "Deep work",
            "description": "Write the plan\n\n[ticktick:t9]",
            "start": {"dateTime": "2026-11-24T19:00:00+01:00", "timeZone": "Europe/Berlin"},
            "end": {"dateTime": "2026-11-24T19:45:00+01:00", "timeZone": "Europe/Berlin"},
        }
        patch = mapping.tt_patch_from_event(event, TIMED)
        self.assertEqual(patch["dueDate"], "2026-11-24T19:00:00+0100")
        self.assertEqual(patch["_duration_minutes"], 45)
        self.assertFalse(patch["isAllDay"])

    def test_cancelled_event_detected(self):
        cancelled = {
            "status": "cancelled",
            "extendedProperties": {"private": {"tt_id": "t9"}},
        }
        self.assertEqual(mapping.event_id_of(cancelled), "t9")


class TimezoneTests(unittest.TestCase):
    def test_all_day_utc_date_is_not_used(self):
        task = {
            "id": "tz1", "title": "German lesson", "isAllDay": True,
            "dueDate": "2026-08-16T22:00:00.000+0000", "timeZone": "Europe/Berlin",
        }
        payload = mapping.gt_task_payload(task)
        self.assertEqual(payload["due"], "2026-08-17T00:00:00.000Z")
        self.assertEqual(mapping.tt_task_view(task)["due"], "2026-08-17")

    def test_timed_utc_is_converted_to_local(self):
        task = {
            "id": "tz2", "title": "WC tabs", "isAllDay": False,
            "dueDate": "2026-09-28T21:00:00.000+0000", "timeZone": "Europe/Berlin",
        }
        payload = mapping.event_payload(task, 30)
        self.assertEqual(payload["start"]["dateTime"], "2026-09-28T23:00:00+02:00")
        self.assertEqual(payload["end"]["dateTime"], "2026-09-28T23:30:00+02:00")

    def test_summer_and_winter_offsets(self):
        summer = {"id": "s", "dueDate": "2026-07-01T10:00:00+0000", "timeZone": "Europe/Berlin"}
        winter = {"id": "w", "dueDate": "2026-12-01T10:00:00+0000", "timeZone": "Europe/Berlin"}
        self.assertTrue(mapping.event_payload(summer, 15)["start"]["dateTime"].endswith("+02:00"))
        self.assertTrue(mapping.event_payload(winter, 15)["start"]["dateTime"].endswith("+01:00"))


class RecurrenceTests(unittest.TestCase):
    RULE = "RRULE:FREQ=WEEKLY;WKST=SU;INTERVAL=2;BYDAY=TH"

    def test_rrule_passes_through(self):
        task = dict(TIMED, repeatFlag=self.RULE)
        payload = mapping.event_payload(task, 15)
        self.assertEqual(payload["recurrence"], [self.RULE])
        self.assertEqual(mapping.desired_event_view(task, 15)["rrule"],
                         mapping.rrule_key([self.RULE]))

    def test_non_recurring_has_no_recurrence(self):
        self.assertEqual(mapping.recurrence_of(TIMED), [])
        self.assertNotIn("recurrence", mapping.event_payload(TIMED, 15))
        self.assertIsNone(mapping.desired_event_view(TIMED, 15)["rrule"])

    def test_instance_exception_is_not_a_change(self):
        task = dict(TIMED, repeatFlag=self.RULE)
        payload = mapping.event_payload(task, 15)
        echo = {
            "summary": payload["summary"],
            "start": payload["start"],
            "end": payload["end"],
            "description": payload["description"],
            "recurrence": [self.RULE, "EXDATE;TZID=Europe/Berlin:20261126T173000"],
            "status": "confirmed",
        }
        view = mapping.event_view(echo)
        view.pop("status")
        self.assertEqual(view, mapping.desired_event_view(task, 15))

    def test_rrule_key_ignores_parameter_order(self):
        self.assertEqual(
            mapping.rrule_key(["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=SU"]),
            mapping.rrule_key(["RRULE:BYDAY=SU;INTERVAL=1;FREQ=WEEKLY"]),
        )


if __name__ == "__main__":
    unittest.main()
