"""Configuration loading with defaults and validation."""

import copy
import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULTS = {
    "ticktick": {"api_base": "https://api.ticktick.com/open/v1"},
    "google": {
        "tasks": {"api_base": "https://tasks.googleapis.com/tasks/v1"},
        "calendar": {
            "api_base": "https://www.googleapis.com/calendar/v3",
            "id": "primary",
            "timezone": "Europe/Berlin",
            "lookback_days": 30,
            "lookahead_days": 365,
        },
        "oauth_app_status": "testing",
    },
    "duration": {"default_minutes": 30, "max_minutes": 480},
    "timed_tasks": {
        "transparency": "opaque",
        "on_complete": "delete",
        "on_event_delete": "untime",
    },
    "lists": {"include": [], "exclude": []},
    "no_date": {"include": False},
    "on_delete": "mirror",
    "on_disappear": "complete",
    "conflicts": "newest",
    "recurring": "series",
    "import_unmarked_events": False,
    "purge_tombstones_days": 30,
    "dry_run": False,
}

VALID_ON_DELETE = {"mirror", "resurrect", "ignore"}
VALID_ON_EVENT_DELETE = {"untime", "ignore", "delete"}
VALID_ON_COMPLETE = {"delete", "keep"}
VALID_CONFLICTS = {"newest", "tt", "gt"}
VALID_RECURRING = {"series", "skip"}
VALID_ON_DISAPPEAR = {"complete", "delete"}
VALID_OAUTH_APP_STATUS = {"testing", "published"}

# Google expires refresh tokens after 7 days while the OAuth app is in Testing.
TESTING_REFRESH_WARN_DAYS = 6.5


def google_refresh_expiring(cfg, age_days):
    """True when a Testing-mode Google refresh token is about to die."""
    return (
        cfg["google"]["oauth_app_status"] == "testing"
        and age_days is not None
        and age_days >= TESTING_REFRESH_WARN_DAYS
    )


def _merge(base, overlay):
    result = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path=None):
    data = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    cfg = _merge(DEFAULTS, data)
    _validate(cfg)
    return cfg


def _validate(cfg):
    if cfg["on_delete"] not in VALID_ON_DELETE:
        raise ValueError(f"on_delete must be one of {sorted(VALID_ON_DELETE)}")
    if cfg["timed_tasks"]["on_event_delete"] not in VALID_ON_EVENT_DELETE:
        raise ValueError(f"timed_tasks.on_event_delete must be one of {sorted(VALID_ON_EVENT_DELETE)}")
    if cfg["timed_tasks"]["on_complete"] not in VALID_ON_COMPLETE:
        raise ValueError(f"timed_tasks.on_complete must be one of {sorted(VALID_ON_COMPLETE)}")
    if cfg["conflicts"] not in VALID_CONFLICTS:
        raise ValueError(f"conflicts must be one of {sorted(VALID_CONFLICTS)}")
    if cfg["recurring"] not in VALID_RECURRING:
        raise ValueError(f"recurring must be one of {sorted(VALID_RECURRING)}")
    if cfg["on_disappear"] not in VALID_ON_DISAPPEAR:
        raise ValueError(f"on_disappear must be one of {sorted(VALID_ON_DISAPPEAR)}")
    if cfg["google"]["oauth_app_status"] not in VALID_OAUTH_APP_STATUS:
        raise ValueError(
            f"google.oauth_app_status must be one of {sorted(VALID_OAUTH_APP_STATUS)}"
        )
    tz_name = cfg["google"]["calendar"]["timezone"]
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"google.calendar.timezone {tz_name!r} is not a known IANA zone") from exc
    if int(cfg["duration"]["default_minutes"]) <= 0:
        raise ValueError("duration.default_minutes must be positive")
    if int(cfg["duration"]["max_minutes"]) < int(cfg["duration"]["default_minutes"]):
        raise ValueError("duration.max_minutes must be >= duration.default_minutes")


def duration_minutes(cfg, task):
    from .duration import find_minutes

    return find_minutes(
        task.get("tags") or [],
        text=f"{task.get('title', '')} {task.get('content', '')}",
        default=int(cfg["duration"]["default_minutes"]),
        maximum=int(cfg["duration"]["max_minutes"]),
    )
