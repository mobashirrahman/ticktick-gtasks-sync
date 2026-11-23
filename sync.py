#!/usr/bin/env python3
"""TickTick ↔ Google Tasks + Calendar sync.

Usage:
  sync.py                     run one reconciliation
  sync.py --dry-run           show intended writes without performing them
  sync.py auth ticktick --client-id ID --client-secret SECRET
  sync.py auth google --client-json path/to/client_secret.json
  sync.py status              recent runs
"""

import argparse
import fcntl
import http.server
import json
import logging
import os
import sys
import time
import urllib.parse

from ttgtsync import config as config_mod
from ttgtsync import oauth
from ttgtsync.store import Store
from ttgtsync.syncrun import Syncer

DEFAULT_CONFIG = os.environ.get(
    "TTGTSYNC_CONFIG", os.path.expanduser("~/.config/ttgtsync/config.json")
)
DEFAULT_SECRETS = os.environ.get(
    "TTGTSYNC_SECRETS", os.path.expanduser("~/.config/ttgtsync/secrets.json")
)
DEFAULT_STATE = os.environ.get(
    "TTGTSYNC_STATE", os.path.expanduser("~/.local/state/ttgtsync/state.db")
)

log = logging.getLogger("ttgtsync")


def setup_logging(verbose=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def cmd_run(args):
    cfg = config_mod.load(args.config)
    if args.dry_run:
        cfg["dry_run"] = True
    lock_path = f"{args.state}.lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.info("another run holds %s; skipping this tick", lock_path)
            print(json.dumps({"status": "busy", "stats": {}}))
            return 0
        secrets = oauth.Secrets(args.secrets)
        store = Store(args.state)
        try:
            syncer = Syncer(cfg, secrets, store, dry_run=args.dry_run)
            status, stats = syncer.run()
        finally:
            store.close()
    print(json.dumps({"status": status, "stats": stats}, indent=2, sort_keys=True))
    return 0 if status == "ok" else 1


def cmd_status(args):
    store = Store(args.state)
    try:
        runs = store.last_runs(5)
        pairs = store.pairs(enabled_only=False)
    finally:
        store.close()
    secrets = oauth.Secrets(args.secrets)
    tokens = {}
    for app in ("ticktick", "google"):
        age = secrets.refresh_age_days(app)
        tokens[app] = {
            "refresh_age_days": round(age, 1) if age is not None else None,
            "access_expires_in_days": (
                round(secrets.access_expiry_days(app), 1)
                if secrets.access_expiry_days(app) is not None else None
            ),
            "reauth_soon": bool(
                (app == "google" and age is not None and age >= 6.5)
                or secrets.needs_reauth(app)
            ),
        }
    print(json.dumps({"pairs": pairs, "runs": runs, "tokens": tokens}, indent=2, sort_keys=True))
    return 0


def cmd_auth(args):
    secrets = oauth.Secrets(args.secrets)
    redirect_uri = f"http://localhost:{args.port}/callback"
    if args.provider == "ticktick":
        stored = secrets.entry("ticktick")
        client_id = args.client_id or stored.get("client_id")
        client_secret = args.client_secret or stored.get("client_secret")
        if not client_id or not client_secret:
            print("auth ticktick needs --client-id/--client-secret or stored credentials",
                  file=sys.stderr)
            return 2
        url = oauth.authorize_url_ticktick(client_id, redirect_uri)
    else:
        client_id, client_secret = _google_credentials(args, secrets)
        url = oauth.authorize_url_google(client_id, redirect_uri)
        secrets.update("google", client_id=client_id, client_secret=client_secret)

    print(f"\nOpen this URL in a browser on this machine:\n\n{url}\n", flush=True)
    print(f"Waiting for the redirect on {redirect_uri} (Ctrl-C to abort)…", flush=True)
    code = _wait_for_code(args.port, args.timeout)
    if not code:
        print("no authorization code received", file=sys.stderr)
        return 1

    if args.provider == "ticktick":
        tokens = oauth.exchange_ticktick(code, client_id, client_secret, redirect_uri)
        secrets.update(
            "ticktick",
            client_id=client_id,
            client_secret=client_secret,
            access_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_type=tokens.get("token_type"),
        )
    else:
        tokens = oauth.exchange_google(code, client_id, client_secret, redirect_uri)
        secrets.update(
            "google",
            access_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_type=tokens.get("token_type"),
        )
    entry = secrets.entry(args.provider)
    entry["expires_at"] = _expiry(tokens)
    secrets.save()
    print(f"{args.provider}: authorized. Refresh token stored in {args.secrets}")
    return 0


def _expiry(tokens):
    return time.time() + float(tokens.get("expires_in", 3600)) - 60


def _google_credentials(args, secrets):
    if args.client_json:
        return _google_client(args.client_json)
    if args.client_id and args.client_secret:
        return args.client_id, args.client_secret
    stored = secrets.entry("google")
    if stored.get("client_id") and stored.get("client_secret"):
        return stored["client_id"], stored["client_secret"]
    raise SystemExit(
        "auth google needs --client-json, --client-id/--client-secret, or stored credentials"
    )


def _google_client(path):
    if not path:
        raise SystemExit("auth google needs --client-json (Desktop app OAuth client JSON)")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    block = data.get("installed") or data.get("web") or data
    return block["client_id"], block["client_secret"]


def _wait_for_code(port, timeout):
    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if "code" in query:
                captured["code"] = query["code"][0]
                body = b"<html><body><h3>Authorized. You can close this tab.</h3></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"missing code")

        def log_message(self, *args):
            return

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SystemExit(f"cannot bind port {port}: {exc}") from exc
    server.timeout = 1
    deadline = time.time() + timeout
    while captured.get("code") is None:
        server.handle_request()
        if time.time() > deadline:
            break
    server.server_close()
    return captured.get("code")


def main(argv=None):
    parser = argparse.ArgumentParser(description="TickTick ↔ Google sync")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--secrets", default=DEFAULT_SECRETS)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run one reconciliation")
    run.add_argument("--config", default=argparse.SUPPRESS)
    run.add_argument("--secrets", default=argparse.SUPPRESS)
    run.add_argument("--state", default=argparse.SUPPRESS)
    run.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    status = sub.add_parser("status", help="recent run history")
    status.add_argument("--state", default=argparse.SUPPRESS)

    auth = sub.add_parser("auth", help="one-time OAuth")
    auth.add_argument("provider", choices=["ticktick", "google"])
    auth.add_argument("--client-id")
    auth.add_argument("--client-secret")
    auth.add_argument("--client-json")
    auth.add_argument("--port", type=int, default=8080)
    auth.add_argument("--timeout", type=int, default=300)
    auth.add_argument("--secrets", default=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "status":
        return cmd_status(args)
    if args.command == "auth":
        return cmd_auth(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
