"""Pure field mapping between TickTick, Google Tasks and Google Calendar."""

import re
from zoneinfo import ZoneInfo

from . import duration as dur
from .util import add_minutes, date_only, gt_due_from_date, parse_iso, rfc3339, tt_format

DEFAULT_TZ = "Europe/Berlin"

PRIORITY_NAMES = {0: "none", 1: "low", 3: "medium", 5: "high"}
PRIORITY_VALUES = {name: value for value, name in PRIORITY_NAMES.items()}

MARKER_TT = re.compile(r"^\[tt:([^\]]+)\]\s*$", re.MULTILINE)
MARKER_ANY = re.compile(r"^[ \t]*\[(?:tt|ticktick):[^\]]+\][ \t]*$", re.MULTILINE)
MARKER_PRIORITY = re.compile(r"^\[priority:(none|low|medium|high)\]\s*$", re.MULTILINE)
MARKER_START = re.compile(r"^\[start:([^\]]+)\]\s*$", re.MULTILINE)


def to_zone(moment, tz_name, fallback=DEFAULT_TZ):
    if moment is None:
        return None
    for candidate in (tz_name, fallback):
        if not candidate:
            continue
        try:
            return moment.astimezone(ZoneInfo(candidate))
        except Exception:
            continue
    return moment


def tt_due_local(task, default_tz=DEFAULT_TZ):
    """Due moment in the task's own timezone.

    TickTick stores an all-day task as midnight local time expressed in UTC
    (e.g. 2026-08-16T22:00:00+0000 is 2026-08-17 in Europe/Berlin), so the
    UTC date is wrong by one day for most of the year.
    """
    return to_zone(parse_iso(task.get("dueDate")), task.get("timeZone") or default_tz, default_tz)


def _tags_line(tags):
    tags = [t if str(t).startswith("#") else f"#{t}" for t in (tags or []) if t]
    return " ".join(tags)


def _norm_notes(text):
    """Whitespace-insensitive form so encoding round-trips do not look like changes."""
    lines = [line.rstrip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line.strip())


def encode_notes(task):
    """Notes text written into Google for a TickTick task."""
    parts = []
    content = MARKER_ANY.sub("", task.get("content") or "").strip()
    if content:
        parts.append(content)
    extras = []
    tags = [tag for tag in dur.plain_tags(task.get("tags")) if tag and str(tag) not in content]
    if tags:
        extras.append(_tags_line(tags))
    priority = PRIORITY_NAMES.get(task.get("priority", 0))
    if priority and priority != "none":
        extras.append(f"[priority:{priority}]")
    if task.get("startDate"):
        extras.append(f"[start:{task['startDate']}]")
    for item in task.get("items") or []:
        mark = "x" if item.get("status") == 1 else " "
        extras.append(f"- [{mark}] {item.get('title', '').strip()}")
    if extras:
        parts.append("\n".join(extras))
    return "\n\n".join(parts)


def decode_notes(notes, default_tz="Europe/Berlin"):
    """Split Google notes back into TickTick fields (marker-aware)."""
    text = notes or ""
    result = {"content": "", "tags": [], "priority": 0, "startDate": None, "items": []}

    priority_match = MARKER_PRIORITY.search(text)
    if priority_match:
        result["priority"] = PRIORITY_VALUES.get(priority_match.group(1), 0)
        text = MARKER_PRIORITY.sub("", text)

    start_match = MARKER_START.search(text)
    if start_match:
        result["startDate"] = start_match.group(1)
        text = MARKER_START.sub("", text)

    result["content"] = MARKER_ANY.sub("", text).strip()
    return result


def strip_marker(text):
    return MARKER_ANY.sub("", text or "").strip()


def with_marker(notes, tt_id):
    base = strip_marker(notes)
    return f"{base}\n\n[tt:{tt_id}]".strip() if base else f"[tt:{tt_id}]"


def marker_id(notes):
    match = MARKER_TT.search(notes or "")
    return match.group(1) if match else None


def gt_task_payload(task, default_tz=DEFAULT_TZ):
    """Google Tasks insert/patch body for an untimed TickTick task."""
    notes = encode_notes(task)
    due = None
    moment = tt_due_local(task, default_tz)
    if moment is not None:
        due = gt_due_from_date(date_only(moment))
    return {
        "title": (task.get("title") or "").strip() or "(untitled)",
        "notes": with_marker(notes, task["id"]),
        "due": due,
        "status": "completed" if task.get("status") == 2 else "needsAction",
    }


def gt_task_view(gt_task):
    """Comparable projection of a Google task, marker removed."""
    return {
        "title": (gt_task.get("title") or "").strip() or "(untitled)",
        "notes": _norm_notes(strip_marker(gt_task.get("notes"))),
        "due": (gt_task.get("due") or "")[:10] or None,
        "status": "completed" if gt_task.get("status") == "completed" else "needsAction",
    }


def tt_task_view(task, default_tz=DEFAULT_TZ):
    """Comparable projection of a TickTick task in the Tasks domain."""
    moment = tt_due_local(task, default_tz)
    return {
        "title": (task.get("title") or "").strip() or "(untitled)",
        "notes": _norm_notes(encode_notes(task)),
        "due": date_only(moment) if moment else None,
        "status": "completed" if task.get("status") == 2 else "needsAction",
    }


def tt_from_gt(gt_task, project_id, default_tz="Europe/Berlin"):
    """TickTick create/update body for a Google task."""
    decoded = decode_notes(gt_task.get("notes"))
    body = {
        "projectId": project_id,
        "title": (gt_task.get("title") or "").strip() or "(untitled)",
        "content": decoded["content"],
        "priority": decoded["priority"] or 0,
        "status": 0 if gt_task.get("status") != "completed" else 2,
        "isAllDay": True,
    }
    due = (gt_task.get("due") or "")[:10]
    if due:
        body["dueDate"] = f"{due}T00:00:00+0100" if default_tz == "Europe/Berlin" else f"{due}T00:00:00+0000"
        body["timeZone"] = default_tz
    if decoded["startDate"]:
        body["startDate"] = decoded["startDate"]
    if decoded["priority"]:
        body["priority"] = decoded["priority"]
    return body


def recurrence_of(task):
    """TickTick stores RFC 5545 RRULEs; Calendar accepts them verbatim."""
    flag = (task.get("repeatFlag") or "").strip()
    return [flag] if flag.upper().startswith("RRULE:") else []


def rrule_key(recurrence):
    """Normalized first RRULE line.

    Google rewrites the list when a single occurrence is edited (adds EXDATE),
    so compare the rule itself and leave exceptions alone.
    """
    for line in recurrence or []:
        text = (line or "").strip()
        if text.upper().startswith("RRULE:"):
            parts = [part.strip().upper() for part in text[6:].split(";") if part.strip()]
            return "RRULE:" + ";".join(sorted(parts))
    return None


def event_payload(task, minutes, default_tz=DEFAULT_TZ, transparency="opaque"):
    """Google Calendar insert body for a timed TickTick task."""
    moment = tt_due_local(task, default_tz)
    if moment is None:
        raise ValueError("timed task requires dueDate")
    tz = task.get("timeZone") or default_tz
    end = add_minutes(moment, minutes)
    description = encode_notes(task)
    description = f"{description}\n\n[ticktick:{task['id']}]".strip() if description else f"[ticktick:{task['id']}]"
    payload = {
        "summary": (task.get("title") or "").strip() or "(untitled)",
        "description": description,
        "start": {"dateTime": rfc3339(moment), "timeZone": tz},
        "end": {"dateTime": rfc3339(end), "timeZone": tz},
        "transparency": transparency,
        "reminders": {"useDefault": False},
        "extendedProperties": {
            "private": {
                "tt_id": task["id"],
                "tt_project": task.get("projectId", ""),
            }
        },
    }
    recurrence = recurrence_of(task)
    if recurrence:
        payload["recurrence"] = recurrence
    return payload


def event_view(event):
    """Comparable projection of a Calendar event."""
    start = (event.get("start") or {}).get("dateTime")
    end = (event.get("end") or {}).get("dateTime")
    description = _norm_notes(strip_event_marker(event.get("description")))
    return {
        "summary": (event.get("summary") or "").strip() or "(untitled)",
        "start": rfc3339(parse_iso(start)) if start else None,
        "end": rfc3339(parse_iso(end)) if end else None,
        "description": description,
        "rrule": rrule_key(event.get("recurrence")),
        "status": event.get("status") or "confirmed",
    }


def event_id_of(event):
    return (event.get("extendedProperties") or {}).get("private", {}).get("tt_id")


def desired_event_view(task, minutes, default_tz=DEFAULT_TZ):
    """Projection of what the event should contain, comparable with event_view."""
    payload = event_payload(task, minutes, default_tz)
    return {
        "summary": payload["summary"],
        "start": rfc3339(parse_iso(payload["start"]["dateTime"])),
        "end": rfc3339(parse_iso(payload["end"]["dateTime"])),
        "description": _norm_notes(strip_event_marker(payload["description"])),
        "rrule": rrule_key(payload.get("recurrence")),
    }


def strip_event_marker(description):
    """Remove a sync marker wherever Google left it (start, middle or end)."""
    return MARKER_ANY.sub("", description or "").strip()


def tt_patch_from_event(event, current_task, default_tz="Europe/Berlin"):
    """TickTick update body reflecting a moved/resized Calendar block."""
    start = parse_iso((event.get("start") or {}).get("dateTime"))
    end = parse_iso((event.get("end") or {}).get("dateTime"))
    if start is None:
        return {}
    body = {
        "id": current_task["id"],
        "projectId": current_task.get("projectId"),
        "dueDate": tt_format(start),
        "timeZone": (event.get("start") or {}).get("timeZone") or default_tz,
        "isAllDay": False,
    }
    if end is not None:
        minutes = max(1, int(round((end - start).total_seconds() / 60)))
        body["_duration_minutes"] = minutes
    summary = (event.get("summary") or "").strip()
    if summary and summary != current_task.get("title"):
        body["title"] = summary
    description = (event.get("description") or "").strip()
    if description and description != (current_task.get("content") or "").strip():
        decoded = decode_notes(description)
        body["content"] = decoded["content"]
    return body
