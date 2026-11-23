# TickTick ↔ Google Tasks + Calendar sync

Keeps TickTick mirrored into Google, list-for-list, both directions, running as a
systemd user service on a small Ubuntu server.

Two Google surfaces, chosen by task shape:

| TickTick task | Google surface | Two-way partner |
|---|---|---|
| **Timed** (`isAllDay: false`) | **Calendar event** on a dedicated calendar | event start/end/title |
| **Untimed** (date only) | **Google Tasks** task in the paired list | title/notes/due/status |
| No date | TickTick only | — |

Timed tasks are events because Google Tasks **cannot store a time of day** — the
Tasks API discards the time portion of `due`, and Calendar renders the Tasks layer
as all-day. Durations come from tags (`#15min`, `#1hr`, `#60min`).

## Quick start

**Requirements:** Python 3.11+ — standard library only, no `pip install`. Linux or macOS; the timer
uses a systemd user service on Linux.

Runs as a **systemd user service** (`Linger=yes`, so it survives logout). No sudo needed.

| What | Where |
|---|---|
| code | `~/ttgtsync` |
| config | `~/.config/ttgtsync/config.json` |
| secrets | `~/.config/ttgtsync/secrets.json` (600) |
| state | `~/.local/state/ttgtsync/state.db` |
| units | `~/.config/systemd/user/ttgtsync.{service,timer}` |

```bash
# 1. deploy code + units (does not enable the timer)
./deploy/deploy.sh

# 2. install config
scp config.example.json your-server:.config/ttgtsync/config.json

# 3. authorize (see below)
# 4. start the timer once auth works
ssh your-server 'systemctl --user enable --now ttgtsync.timer'
```

Rehearse without writing anything first:

```bash
ssh your-server 'cd ~/ttgtsync && python3 sync.py --dry-run run'
```

## Authorizing

Both providers use a loopback redirect on port 8080, so either run the commands on
the machine with the browser (simplest — then copy the result), or run them on the
server behind a tunnel.

**Simplest path — authorize on your laptop, then copy the tokens:**

```bash
# on the laptop
python3 sync.py auth ticktick
python3 sync.py auth google
scp ~/.config/ttgtsync/secrets.json your-server:.config/ttgtsync/secrets.json
ssh your-server 'chmod 600 ~/.config/ttgtsync/secrets.json'
```

Client credentials stored in `~/.config/ttgtsync/secrets.json` are reused by
`auth` when the flags are omitted, so nothing sensitive lands on a command line.

**Tunneled path — authorize on the server:**

```bash
ssh -L 8080:localhost:8080 your-server
```

**TickTick** — register an app at <https://developer.ticktick.com> with the
redirect URI `http://localhost:8080/callback` in the **OAuth redirect URL** field
(not "App Service URL") and scopes `tasks:read tasks:write`, then:

```bash
cd ~/ttgtsync
python3 sync.py auth ticktick --client-id YOUR_ID --client-secret YOUR_SECRET
```

**Google** — in Google Cloud Console, create a project, enable **Tasks API** and
**Calendar API**, configure the OAuth consent screen with scopes
`https://www.googleapis.com/auth/tasks` and
`https://www.googleapis.com/auth/calendar.events`, create an OAuth client with
redirect `http://localhost:8080/callback`, then either pass the downloaded JSON
(`--client-json`) or store the client id/secret and run `sync.py auth google`.

Publish the Google app to **Production** (or use Internal on Workspace). Apps left in
*Testing* get refresh tokens that expire after 7 days, which breaks the timer weekly.

Tokens land in `~/.config/ttgtsync/secrets.json` (mode 600) and refresh automatically.

### Removing the 7-day refresh-token expiry

While the OAuth app is in **Testing**, Google expires refresh tokens after 7 days. Publishing the
app requires an ownership-verified domain — it does not have to relate to this project, and a
subdomain you control is fine.

1. **Verify the domain** in [Search Console](https://search.google.com/search-console): add a
   *Domain* property and publish the DNS TXT record it gives you. Use the same Google account that
   is Owner/Editor on the Cloud project.
2. **Host a privacy policy** on it (e.g. `https://yourdomain.com/privacy`). `docs/privacy.html` in
   this repo is a ready-to-use page — replace `CONTACT_EMAIL`.
3. **Google Auth Platform → Branding**: set *Authorized domains* to `yourdomain.com` and *Privacy
   policy URL* to the page from step 2.
4. **Audience → Publish app.** You will likely still see *Verification status: Not submitted*.
   That is fine for personal use: consent shows an "unverified app" warning, which you dismiss via
   *Advanced → continue*, and the app is capped at 100 users.
5. **Re-run `sync.py auth google`** once so the stored refresh token is issued under the published
   status.

Publishing status, not verification approval, is what stops the 7-day expiry.

## Running

```bash
python3 sync.py run            # one reconciliation (what the timer does)
python3 sync.py --dry-run run  # log intended writes, change nothing
python3 sync.py status         # last runs, pairs, counters
```

The systemd timer runs every 3 minutes with a 30-second jitter. Each run takes a
`flock` on `<state>.lock`; a run that finds the lock held exits immediately with
`{"status": "busy"}`, so overlapping ticks (or a manual run against the timer) can
never race on the same state database.

## How it stays loop-free

1. **Mapping table, never title matching.** Every task maps to exactly one Google
   object in SQLite (`~/.local/state/ttgtsync/state.db`).
2. **Post-write snapshots.** After any write, the snapshot stored is what the remote
   now reports, so our own write never reads back as a user edit.
3. **Durable markers.** Google Tasks notes end with `[tt:<id>]`; events carry
   `extendedProperties.private.tt_id`. A rebuilt database re-adopts existing pairs
   instead of duplicating them.
4. **Tombstones.** Deleted blocks and completed tasks stay dead until the TickTick
   side actually changes.

Conflicts resolve last-writer-wins (`conflicts: newest`), tie goes to TickTick.

## Domain migrations

Change a task from all-day to timed (or back) and the next run moves it between the
two surfaces: the old Google object is deleted, the new one created, and a tombstone
stops the pair from flip-flopping.

Deleting a calendar block sets `timed_tasks.on_event_delete: untime` by default — the
task loses its time and lands in Google Tasks instead of recreating the block.

## Configuration

See `config.example.json`. Notable keys:

| Key | Default | Meaning |
|---|---|---|
| `google.calendar.id` | `primary` | calendar that receives timed blocks |
| `duration.default_minutes` | 30 | duration when no `#Nmin` tag |
| `duration.max_minutes` | 480 | clamp for absurd tags |
| `timed_tasks.on_complete` | `delete` | remove the block when done |
| `timed_tasks.on_event_delete` | `untime` | `untime` / `ignore` / `delete` |
| `on_delete` | `mirror` | `mirror` / `resurrect` / `ignore` |
| `on_disappear` | `complete` | `complete` (mark Google done) / `delete` |
| `conflicts` | `newest` | `newest` / `tt` / `gt` |
| `recurring` | `series` | `series` (one RRULE event) / `skip` |
| `lists.include` | `[]` | force-include a list by name or id, even if excluded |
| `lists.exclude` | `[]` | skip these lists |

`Inbox` syncs like any other list — TickTick exposes it at project id `inbox`,
even though it never appears in the project listing.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Covers duration parsing, domain classification, both mapping directions, echo
stability (encode → decode → encode must not drift), merge decisions, and the
SQLite state machine.

## Limitations

- Timed tasks have no Google Tasks checkbox; untimed tasks have no time. That is the
  Google Tasks API limit, not a setting.
- **Recurring tasks** become one Calendar event carrying TickTick's RRULE verbatim.
  Editing a single occurrence in Google is ignored (it cannot be expressed in
  TickTick); series-level edits and completion roll-forward do sync. Set
  `recurring: "skip"` to leave them out entirely.
- Untimed recurring tasks mirror as one Google Task whose due date follows the
  current occurrence — Google Tasks cannot repeat.
- Duration is stored in tags; resizing a block rewrites the tag (`#45min`).
- Sync latency is the timer interval, not instant.
- `on_delete: mirror` really deletes. Use `ignore` while you are still trusting it.
- Subtasks/chains and attachments are not mirrored.

## Troubleshooting

```bash
ssh your-server 'journalctl --user -u ttgtsync.service -n 50 --no-pager'
ssh your-server 'cd ~/ttgtsync && python3 sync.py status'
ssh your-server 'systemctl --user list-timers ttgtsync.timer'
```

`401`/`invalid_grant` → re-run the matching `auth` command. `403` on Calendar → the
OAuth grant is missing `calendar.events`; re-authorize Google.
