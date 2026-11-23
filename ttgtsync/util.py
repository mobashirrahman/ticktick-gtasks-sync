"""HTTP, time and hashing helpers shared by the API clients."""

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

USER_AGENT = "ttgtsync/0.1 (+https://commandcode.ai)"


class HttpResult:
    __slots__ = ("status", "headers", "body", "json")

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body
        try:
            self.json = json.loads(body) if body else None
        except ValueError:
            self.json = None

    def ok(self):
        return 200 <= self.status < 300


def request(method, url, headers=None, body=None, timeout=60, retries=3, sleeper=time.sleep):
    """Perform one HTTP request with bounded retries on 429/5xx.

    429 is always retried: the server rejected the request before acting on it.
    5xx and network errors are ambiguous (the write may have landed), so they are
    only retried for idempotent methods; retrying a POST could create duplicates.
    """
    idempotent = method.upper() != "POST"
    data = None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return HttpResult(resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            last = HttpResult(exc.code, dict(exc.headers or {}), text)
            if exc.code == 429:
                delay = _retry_after(last.headers, default=5 * (attempt + 1))
                if attempt < retries:
                    sleeper(min(delay, 60))
                    continue
                return last
            if 500 <= exc.code < 600 and idempotent and attempt < retries:
                sleeper(2 ** attempt)
                continue
            return last
        except (urllib.error.URLError, TimeoutError) as exc:
            last = HttpResult(0, {}, str(exc))
            if idempotent and attempt < retries:
                sleeper(2 ** attempt)
                continue
            return last
    return last


def _retry_after(headers, default):
    raw = None
    for key, value in (headers or {}).items():
        if key.lower() == "retry-after":
            raw = value
            break
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def encode_params(params):
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def utcnow():
    return datetime.now(timezone.utc)


def utcnow_iso():
    return utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    """Parse the ISO-8601 variants TickTick and Google emit, always aware."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def rfc3339(moment):
    return moment.isoformat()


def tt_format(moment):
    """TickTick expects +0100, not +01:00."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S%z")


def date_only(moment):
    return moment.date().isoformat()


def gt_due_from_date(day):
    return f"{day}T00:00:00.000Z"


def sha256_json(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def add_minutes(moment, minutes):
    return moment + timedelta(minutes=int(minutes))
