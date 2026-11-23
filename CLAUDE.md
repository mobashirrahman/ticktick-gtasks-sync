# ttgtsync

Two-way TickTick ⇄ Google Tasks/Calendar sync. `sync.py` is the CLI; logic lives in `ttgtsync/`
(`syncrun.py` orchestrates, `mapping.py` converts, `store.py` is the SQLite state).

## Rules

- **Standard library only.** No third-party runtime dependencies; `pyproject.toml` is tooling
  config only.
- Python 3.11+ (CI: 3.11–3.15). Don't use syntax newer than 3.11.
- Tests make no network calls; fake the API clients / `urllib.request.urlopen`.
- Never retry a `POST` after an ambiguous failure (5xx/timeout) — it duplicates creates.

## Commands

```bash
python3 -m unittest discover -s tests -t .   # tests
ruff check .                                  # lint (config in pyproject.toml)
mypy --ignore-missing-imports ttgtsync sync.py
HOST=ubuntu-server ./deploy/deploy.sh         # deploy committed HEAD (runs tests first)
```

## Production

- Runs on `ubuntu-server` (Tailscale) as a systemd **user** timer every 3 min.
- Code `~/ttgtsync` (replaced wholesale on deploy; `REVISION` holds the commit),
  config/secrets `~/.config/ttgtsync/`, state `~/.local/state/ttgtsync/state.db`.
- Verify a change: `ssh ubuntu-server 'cd ~/ttgtsync && python3 sync.py --dry-run run'`,
  then `journalctl --user -u ttgtsync.service -n 50 --no-pager`.
- `sync.py auth …` needs a browser on port 8080 — the user has to run it.
