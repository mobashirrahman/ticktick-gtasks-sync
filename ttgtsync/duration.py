"""Duration parsing from TickTick tags such as #15min, #1hr, #60min."""

import re

DURATION_RE = re.compile(
    r"#(\d{1,3})\s?(minutes|minute|mins|min|hours|hour|hrs|hr|m|h)\b",
    re.IGNORECASE,
)

TEXT_DURATION_RE = re.compile(
    r"(?<![\w#])(\d{1,3})\s?(minutes|minute|mins|min|hours|hour|hrs|hr|m|h)\b",
    re.IGNORECASE,
)

_UNIT_MINUTES = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
}


def tag_minutes(tag):
    """Minutes encoded by a single tag, or None."""
    match = DURATION_RE.fullmatch(tag.strip())
    if not match:
        return None
    return int(match.group(1)) * _UNIT_MINUTES[match.group(2).lower()]


def find_minutes(tags, text="", default=30, maximum=480):
    """Resolve a task duration: first duration tag, then text, then default."""
    for tag in tags or []:
        minutes = tag_minutes(tag) if tag else None
        if minutes:
            return _clamp(minutes, default, maximum)
    if text:
        match = TEXT_DURATION_RE.search(text)
        if match:
            minutes = int(match.group(1)) * _UNIT_MINUTES[match.group(2).lower()]
            if minutes:
                return _clamp(minutes, default, maximum)
    return _clamp(default, default, maximum)


def _clamp(minutes, default, maximum):
    if minutes <= 0:
        return default
    return min(int(minutes), int(maximum))


def duration_tag(minutes):
    return f"#{int(minutes)}min"


def replace_duration_tag(tags, minutes):
    """Replace the first duration tag, appending one when none exists."""
    cleaned = []
    replaced = False
    for tag in tags or []:
        if tag_minutes(tag):
            if replaced:
                continue
            cleaned.append(duration_tag(minutes))
            replaced = True
        else:
            cleaned.append(tag)
    if not replaced:
        cleaned.append(duration_tag(minutes))
    return cleaned


def plain_tags(tags):
    return [tag for tag in (tags or []) if tag and not tag_minutes(tag)]
