"""Google Tasks API client."""

import urllib.parse

from .oauth import Secrets
from .ticktick import ApiError
from .util import encode_params, request


class GoogleTasks:
    def __init__(self, cfg, secrets: Secrets):
        self.base = cfg["google"]["tasks"]["api_base"].rstrip("/")
        self.secrets = secrets

    def _call(self, method, path, body=None, query=None):
        token = self.secrets.access_token("google")
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{encode_params(query)}"
        result = request(method, url, headers={"Authorization": f"Bearer {token}"}, body=body)
        if not result.ok():
            raise ApiError("gtasks", result.status, (result.body or "")[:500])
        return result.json if result.body else None

    def tasklists(self):
        items, page = [], None
        while True:
            payload = self._call("GET", "/users/@me/lists", query={"maxResults": 100, "pageToken": page}) or {}
            items.extend(payload.get("items") or [])
            page = payload.get("nextPageToken")
            if not page:
                return items

    def create_tasklist(self, title):
        return self._call("POST", "/users/@me/lists", body={"title": title})

    def tasks(self, list_id, updated_min=None, show_completed=True, show_hidden=True):
        items, page = [], None
        while True:
            query = {
                "maxResults": 100,
                "showCompleted": str(bool(show_completed)).lower(),
                "showHidden": str(bool(show_hidden)).lower(),
                "pageToken": page,
            }
            if updated_min:
                query["updatedMin"] = updated_min
            payload = self._call(
                "GET", f"/lists/{_q(list_id)}/tasks", query=query
            ) or {}
            items.extend(payload.get("items") or [])
            page = payload.get("nextPageToken")
            if not page:
                return items

    def insert_task(self, list_id, body):
        return self._call("POST", f"/lists/{_q(list_id)}/tasks", body=body)

    def patch_task(self, list_id, task_id, body, update_mask=None):
        query = {"updateMask": update_mask} if update_mask else None
        return self._call("PATCH", f"/lists/{_q(list_id)}/tasks/{_q(task_id)}", body=body, query=query)

    def delete_task(self, list_id, task_id):
        return self._call("DELETE", f"/lists/{_q(list_id)}/tasks/{_q(task_id)}")


def _q(value):
    return urllib.parse.quote(str(value), safe="")
