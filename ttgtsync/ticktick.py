"""TickTick Open API v1 client."""

import urllib.parse

from .oauth import Secrets
from .util import encode_params, request


class ApiError(RuntimeError):
    def __init__(self, service, status, message):
        super().__init__(f"{service} {status}: {message}")
        self.service = service
        self.status = status
        self.message = message


class TickTick:
    def __init__(self, cfg, secrets: Secrets):
        self.base = cfg["ticktick"]["api_base"].rstrip("/")
        self.secrets = secrets

    def _call(self, method, path, body=None, query=None):
        token = self.secrets.access_token("ticktick")
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{encode_params(query)}"
        result = request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            body=body,
        )
        if not result.ok():
            raise ApiError("ticktick", result.status, (result.body or "")[:500])
        return result.json if result.body else None

    def projects(self):
        return self._call("GET", "/project") or []

    def project_data(self, project_id):
        return self._call("GET", f"/project/{_q(project_id)}/data") or {}

    def create_task(self, body):
        return self._call("POST", "/task", body=body)

    def update_task(self, body):
        task_id = body.get("id")
        if not task_id:
            raise ValueError("update_task requires id")
        payload = {key: value for key, value in body.items() if not key.startswith("_")}
        return self._call("POST", f"/task/{_q(task_id)}", body=payload)

    def complete_task(self, project_id, task_id):
        return self._call("POST", f"/project/{_q(project_id)}/task/{_q(task_id)}/complete")

    def delete_task(self, project_id, task_id):
        return self._call("DELETE", f"/project/{_q(project_id)}/task/{_q(task_id)}")


def _q(value):
    return urllib.parse.quote(str(value), safe="")
