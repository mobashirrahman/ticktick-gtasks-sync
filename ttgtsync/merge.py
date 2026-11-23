"""Pure change-detection and conflict decisions."""

from .util import canonical, parse_iso

NONE = "none"
PUSH_DOWN = "push_down"
PUSH_UP = "push_up"


def changed(previous, current):
    """A side changed when there is no snapshot yet, or the projection differs."""
    if previous is None:
        return True
    return canonical(previous) != canonical(current)


def _moment(value):
    parsed = parse_iso(value)
    return parsed.timestamp() if parsed else 0.0


def resolve(tt_changed, gt_changed, policy="newest", tt_stamp=None, gt_stamp=None):
    """Pick a direction; returns (action, conflict)."""
    if not tt_changed and not gt_changed:
        return NONE, False
    if tt_changed and not gt_changed:
        return PUSH_DOWN, False
    if gt_changed and not tt_changed:
        return PUSH_UP, False

    if policy == "tt":
        return PUSH_DOWN, True
    if policy == "gt":
        return PUSH_UP, True
    if _moment(tt_stamp) >= _moment(gt_stamp):
        return PUSH_DOWN, True
    return PUSH_UP, True


def out_of_domain(recorded_domain, current_domain):
    """True when a task must migrate between the Calendar and Tasks surfaces."""
    return recorded_domain is not None and recorded_domain != current_domain
