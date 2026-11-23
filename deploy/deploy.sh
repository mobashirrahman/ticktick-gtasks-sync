#!/usr/bin/env bash
# Deploy the sync service to the Ubuntu server over Tailscale (user scope, no sudo).
#
# - Ships only committed files (`git archive HEAD`), so local secrets, state
#   databases, venvs and macOS `._*` files can never leak to the server.
# - Replaces ~/ttgtsync wholesale, so files deleted from the repo disappear there too.
#   Config, secrets and state live outside it (~/.config, ~/.local/state).
# - Swaps the code while holding the run lock, so a timer tick never sees half a tree.
# - Never enables the timer on a fresh install: configure, authorize and dry-run
#   first (see README). An already-enabled timer is restarted on the new code.
set -euo pipefail

HOST="${HOST:?set HOST to your server, e.g. HOST=ubuntu@myserver}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SRC"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "!! working tree has uncommitted changes; deploying HEAD ($(git rev-parse --short HEAD)) only" >&2
fi

echo "==> running tests locally"
python3 -m unittest discover -s tests -t . -q

REV="$(git rev-parse --short HEAD)"
echo "==> shipping ${REV} to ${HOST}"
ssh "$HOST" 'mkdir -p ~/.config/ttgtsync ~/.local/state/ttgtsync ~/.config/systemd/user \
  && chmod 700 ~/.config/ttgtsync ~/.local/state/ttgtsync \
  && rm -rf ~/ttgtsync.new && mkdir ~/ttgtsync.new'
git archive --format=tar HEAD | ssh "$HOST" 'tar -xf - -C ~/ttgtsync.new'
ssh "$HOST" "echo ${REV} > ~/ttgtsync.new/REVISION"

echo "==> swapping code under the run lock"
ssh "$HOST" 'flock ~/.local/state/ttgtsync/state.db.lock sh -c "
  rm -rf ~/ttgtsync.old
  if [ -d ~/ttgtsync ]; then mv ~/ttgtsync ~/ttgtsync.old; fi
  mv ~/ttgtsync.new ~/ttgtsync
  rm -rf ~/ttgtsync.old
"'

echo "==> installing user units"
scp -q deploy/ttgtsync.service deploy/ttgtsync.timer "${HOST}:.config/systemd/user/"
ssh "$HOST" 'systemctl --user daemon-reload'

if ssh "$HOST" 'systemctl --user is-enabled --quiet ttgtsync.timer'; then
  echo "==> timer already enabled; restarting it"
  ssh "$HOST" 'systemctl --user restart ttgtsync.timer'
  ssh "$HOST" 'systemctl --user list-timers ttgtsync.timer --no-pager'
else
  cat <<EOF
==> timer NOT enabled (fresh install). Next steps:
    1. put config.json and secrets.json in ~/.config/ttgtsync/ on ${HOST}
    2. ssh ${HOST} 'cd ~/ttgtsync && python3 sync.py --dry-run run'
    3. ssh ${HOST} 'systemctl --user enable --now ttgtsync.timer'
EOF
fi
