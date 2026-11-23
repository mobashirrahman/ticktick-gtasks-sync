#!/usr/bin/env bash
# Deploy the sync service to the Ubuntu server over Tailscale (user scope, no sudo).
# Uses a tar stream because the server has no rsync.
set -euo pipefail

HOST="${HOST:?set HOST to your server, e.g. HOST=ubuntu@myserver}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> preparing directories on ${HOST}"
ssh "$HOST" 'mkdir -p ~/ttgtsync ~/.config/ttgtsync ~/.local/state/ttgtsync ~/.config/systemd/user'

echo "==> syncing code"
( cd "$SRC" && COPYFILE_DISABLE=1 tar --no-xattrs --exclude='__pycache__' --exclude='.git' --exclude='.DS_Store' -czf - . ) \
  | ssh "$HOST" 'tar -xzf - -C ~/ttgtsync'

echo "==> installing user units"
scp -q "$SRC/deploy/ttgtsync.service" "$SRC/deploy/ttgtsync.timer" "${HOST}:.config/systemd/user/"

echo "==> reloading systemd --user"
ssh "$HOST" 'systemctl --user daemon-reload && systemctl --user enable --now ttgtsync.timer && systemctl --user restart ttgtsync.timer'

echo "==> timer status"
ssh "$HOST" 'systemctl --user list-timers ttgtsync.timer --no-pager'
