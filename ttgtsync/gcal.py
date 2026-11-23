"""Google Calendar API client (events only)."""

import urllib.parse

from .oauth import Secrets
from .ticktick import ApiError
from .util import encode_params, request


class GoogleCalendar:
    def __init__(self, cfg, secrets: Secrets):
        self.base = cfg["google"]["calendar"]["api_base"].rstrip("/")
        self.secrets = secrets

    def _call(self, method, path, body=None, query=None):
        token = self.secrets.access_token("google")
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{encode_params(query)}"
        result = request(method, url, headers={"Authorization": f"Bearer {token}"}, body=body)
        if not result.ok():
            raise ApiError("gcal", result.status, (result.body or "")[:500])
        return result.json if result.body else None

    def calendars(self):
        payload = self._call("GET", "/users/me/calendarList", query={"maxResults": 250}) or {}
        return payload.get("items") or []

    def calendar(self, calendar_id):
        return self._call("GET", f"/calendars/{_q(calendar_id)}")

    def events(self, calendar_id, updated_min=None, time_min=None, time_max=None,
               show_deleted=True, single_events=False):
        """List events. Masters (single_events=False) are what recurring sync compares."""
        items, page = [], None
        while True:
            query = {
                "singleEvents": str(bool(single_events)).lower(),
                "showDeleted": str(bool(show_deleted)).lower(),
                "maxResults": 250,
                "pageToken": page,
            }
            if updated_min:
                query["updatedMin"] = updated_min
            if time_min:
                query["timeMin"] = time_min
            if time_max:
                query["timeMax"] = time_max
            payload = self._call(
                "GET", f"/calendars/{_q(calendar_id)}/events", query=query
            ) or {}
            items.extend(payload.get("items") or [])
            page = payload.get("nextPageToken")
            if not page:
                return items

    def get_event(self, calendar_id, event_id):
        return self._call("GET", f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}")

    def insert_event(self, calendar_id, body):
        return self._call("POST", f"/calendars/{_q(calendar_id)}/events", body=body)

    def patch_event(self, calendar_id, event_id, body, update_mask=None):
        query = {"updateMask": update_mask} if update_mask else None
        return self._call(
            "PATCH", f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}", body=body, query=query
        )

    def delete_event(self, calendar_id, event_id):
        return self._call("DELETE", f"/calendars/{_q(calendar_id)}/events/{_q(event_id)}")


def _q(value):
    return urllib.parse.quote(str(value), safe="")
