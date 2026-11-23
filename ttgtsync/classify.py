"""Decide which Google surface a TickTick task belongs to."""

from .util import parse_iso

CALENDAR = "calendar"
TASKS = "tasks"


def is_timed(task):
    """True when the task has a real time of day, not just a date."""
    if not task.get("dueDate"):
        return False
    if "isAllDay" in task and task["isAllDay"] is not None:
        return not bool(task["isAllDay"])
    moment = parse_iso(task["dueDate"])
    if moment is None:
        return False
    return not (moment.hour == 0 and moment.minute == 0 and moment.second == 0)


def domain_of(task, include_undated=False):
    """'calendar' for timed tasks, 'tasks' for dated all-day, None when undated."""
    if not task.get("dueDate"):
        return TASKS if include_undated else None
    return CALENDAR if is_timed(task) else TASKS


def is_recurring(task):
    return bool(task.get("repeatFlag"))


def is_completed(task):
    return task.get("status") == 2


def should_skip(task, skip_recurring=True):
    if task.get("deleted") is True:
        return True
    if skip_recurring and is_recurring(task):
        return True
    return False
