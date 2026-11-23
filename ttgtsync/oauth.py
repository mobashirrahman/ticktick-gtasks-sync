"""OAuth token storage and refresh for TickTick and Google."""

import base64
import hashlib
import json
import os
import secrets as secrets_mod
import time
import urllib.error
import urllib.parse
import urllib.request

TICKTICK_TOKEN_URL = "https://ticktick.com/oauth/token"
TICKTICK_AUTH_URL = "https://ticktick.com/oauth/authorize"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar.events",
]
TICKTICK_SCOPES = ["tasks:read", "tasks:write"]


class TokenError(RuntimeError):
    pass


def form_post(url, fields, basic_auth=None, timeout=60):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "ttgtsync/0.1",
    }
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise TokenError(f"{url} returned {exc.code}: {detail[:400]}") from exc


def new_state():
    """Unguessable per-attempt OAuth state, checked on the callback (CSRF guard)."""
    return secrets_mod.token_urlsafe(32)


def new_pkce_pair():
    """(verifier, S256 challenge) per RFC 7636."""
    verifier = secrets_mod.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url_ticktick(client_id, redirect_uri, state):
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(TICKTICK_SCOPES),
            "state": state,
        }
    )
    return f"{TICKTICK_AUTH_URL}?{query}"


def authorize_url_google(client_id, redirect_uri, state, code_challenge):
    query = urllib.parse.urlencode(
        {
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}"


def exchange_ticktick(code, client_id, client_secret, redirect_uri):
    return form_post(
        TICKTICK_TOKEN_URL,
        {"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri, "scope": " ".join(TICKTICK_SCOPES)},
        basic_auth=(client_id, client_secret),
    )


def exchange_google(code, client_id, client_secret, redirect_uri, code_verifier):
    return form_post(
        GOOGLE_TOKEN_URL,
        {
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )


class Secrets:
    """secrets.json holder that refreshes access tokens on demand."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    def save(self):
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{self.path}.tmp"
        # Create the file 0600 from the start so tokens are never world-readable,
        # not even between open() and a later chmod().
        if os.path.exists(tmp):
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def entry(self, app):
        return self.data.setdefault(app, {})

    def update(self, app, **fields):
        if fields.get("refresh_token"):
            fields["refresh_issued_at"] = time.time()
        self.entry(app).update(fields)
        self.save()

    def refresh_age_days(self, app):
        issued = self.entry(app).get("refresh_issued_at")
        if not issued:
            return None
        return (time.time() - float(issued)) / 86400.0

    def access_expiry_days(self, app):
        expires = self.entry(app).get("expires_at")
        if not expires:
            return None
        return (float(expires) - time.time()) / 86400.0

    def needs_reauth(self, app, warn_days=21.0):
        """No refresh token means the access token is the only credential."""
        if self.entry(app).get("refresh_token"):
            return False
        days = self.access_expiry_days(app)
        return days is not None and days < warn_days

    def access_token(self, app, force_refresh=False):
        entry = self.entry(app)
        token = entry.get("access_token")
        expires = float(entry.get("expires_at") or 0)
        if token and not force_refresh and expires - 300 > time.time():
            return token
        refreshed = _REFRESHERS[app](entry)
        entry.update(refreshed)
        if refreshed.get("refresh_token"):
            entry["refresh_issued_at"] = time.time()
            entry["refresh_token"] = refreshed["refresh_token"]
        entry["expires_at"] = time.time() + float(refreshed.get("expires_in", 3600)) - 60
        self.save()
        if not entry.get("access_token"):
            raise TokenError(f"{app}: refresh returned no access_token")
        return entry["access_token"]


def _refresh_ticktick(entry):
    if not entry.get("refresh_token"):
        raise TokenError("ticktick: no refresh_token; run `sync.py auth ticktick`")
    return form_post(
        TICKTICK_TOKEN_URL,
        {"grant_type": "refresh_token", "refresh_token": entry["refresh_token"], "scope": " ".join(TICKTICK_SCOPES)},
        basic_auth=(entry.get("client_id"), entry.get("client_secret")),
    )


def _refresh_google(entry):
    if not entry.get("refresh_token"):
        raise TokenError("google: no refresh_token; run `sync.py auth google`")
    return form_post(
        GOOGLE_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": entry["refresh_token"],
            "client_id": entry.get("client_id"),
            "client_secret": entry.get("client_secret"),
        },
    )


_REFRESHERS = {"ticktick": _refresh_ticktick, "google": _refresh_google}
