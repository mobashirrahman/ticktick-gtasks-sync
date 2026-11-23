# TickTick ⇄ Google Tasks + Calendar

[![tests](https://github.com/mobashirrahman/ticktick-gtasks-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/mobashirrahman/ticktick-gtasks-sync/actions/workflows/tests.yml)

**A free, self-hosted two-way sync between TickTick and Google — so your tasks show up where
you actually look, and stay in sync no matter which side you tick them off from.**

No subscription. No third-party server. Your tokens and your data never leave your own machine.

---

## Why this exists

Two-way sync is the feature every task app puts behind a paywall. TickTick syncs *from* Google
Calendar but not back into Google Tasks. Google Tasks is a one-way street. The bridges that do
offer real two-way sync are subscriptions that sit in the middle — another account, another bill,
another copy of your task list on someone else's server.

This is about 2,000 lines of Python you run yourself. It talks to the TickTick Open API and the Google
Tasks/Calendar APIs directly, keeps the mapping in a local SQLite file, and costs nothing per
month. If it breaks, you can read the code that broke.

It also solves the thing the paid bridges get wrong: **Google Tasks cannot store a time of day**
(the API discards the time portion of `due`), so a 17:30 task becomes an all-day row. This sync
writes timed tasks as **real calendar events** instead, with durations taken from your tags — and
drags, resizes and deletes flow back into TickTick.

---

## What you get

Pick the one that sounds like you.

**"I plan in TickTick but live in Google Calendar."**
Timed tasks appear as blocks at their real time — `Dentist 17:30 #15min` becomes a 15-minute block
from 17:30 to 17:45 on the calendar you choose. Drag it to 19:00 and TickTick's due time moves.
Resize it to 45 minutes and the tag is rewritten to `#45min`.

**"I add tasks from my phone, my watch, or Google Assistant."**
Tasks created in Google Tasks land in the matching TickTick list within three minutes, with the
due date intact, and are marked so they never bounce back and forth.

**"I want one checkbox, not two."**
Complete it in Google Tasks and TickTick shows it done. Complete it in TickTick and Google marks
it complete. No more deciding which app is the "real" list.

**"My routines are recurring."**
`RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH` from TickTick becomes one recurring Calendar event that
Google expands for you — every occurrence visible, no duplicated clutter, and completing the task
rolls the series forward instead of deleting it.

**"I want to keep my data."**
Everything runs on hardware you control. OAuth tokens live in a `600` file on your server, the
mapping lives in SQLite next to it, and no traffic goes anywhere except TickTick and Google.

---

## How it works

```
TickTick  ──►  sync.py  ──►  Google Tasks     (untimed tasks: date-only)
              │      │
              │      └──►  Google Calendar   (timed tasks: real time + duration)
              ◄──────────  both directions
```

A systemd timer runs it every three minutes. Each run is `flock`-protected, so overlapping ticks
can never race on the same state. Every TickTick task maps to exactly one Google object by ID —
never by title — and the run stores a snapshot of what it wrote, so its own writes never read back
as edits from you.

---

## Quick start

**Requirements:** Python 3.11+ — standard library only, no `pip install`. Linux or macOS; the timer
uses a systemd user service on Linux.

Runs as a **systemd user service** (`Linger=yes`, so it survives logout and reboot). No sudo needed.

| What | Where |
|---|---|
| code | `~/ttgtsync` |
| config | `~/.config/ttgtsync/config.json` |
| secrets | `~/.config/ttgtsync/secrets.json` (600) |
| state | `~/.local/state/ttgtsync/state.db` |
| units | `~/.config/systemd/user/ttgtsync.{service,timer}` |

```bash
# 1. deploy code + units (runs tests first; does not enable the timer on a fresh install)
HOST=you@your-server ./deploy/deploy.sh

# 2. install config
scp config.example.json your-server:.config/ttgtsync/config.json

# 3. authorize (see below)
# 4. rehearse with no writes
ssh your-server 'cd ~/ttgtsync && python3 sync.py --dry-run run'
# 5. start the timer once the dry run looks right
ssh your-server 'systemctl --user enable --now ttgtsync.timer'
```

You can also run it on a laptop, a Raspberry Pi, or straight from `cron` — nothing requires
systemd except the bundled timer.

---

## Authorizing

Both providers use a loopback redirect on port 8080, so either run the commands on the machine with
the browser (simplest — then copy the result), or run them on the server behind a tunnel.

**Simplest path — authorize on your laptop, then copy the tokens:**

```bash
# on the laptop
python3 sync.py auth ticktick
python3 sync.py auth google
scp ~/.config/ttgtsync/secrets.json your-server:.config/ttgtsync/secrets.json
ssh your-server 'chmod 600 ~/.config/ttgtsync/secrets.json'
```

Client credentials stored in `~/.config/ttgtsync/secrets.json` are reused by `auth` when the flags
are omitted, so nothing sensitive lands on a command line.

**Tunneled path — authorize on the server:**

```bash
ssh -L 8080:localhost:8080 your-server
```

**TickTick** — register an app at <https://developer.ticktick.com> with the redirect URI
`http://localhost:8080/callback` in the **OAuth redirect URL** field (not "App Service URL") and
scopes `tasks:read tasks:write`, then:

```bash
cd ~/ttgtsync
python3 sync.py auth ticktick --client-id YOUR_ID --client-secret YOUR_SECRET
```

**Google** — in Google Cloud Console, create a project, enable **Tasks API** and **Calendar API**,
configure the OAuth consent screen with scopes `https://www.googleapis.com/auth/tasks` and
`https://www.googleapis.com/auth/calendar.events`, create an OAuth client with redirect
`http://localhost:8080/callback`, then either pass the downloaded JSON (`--client-json`) or store
the client id/secret and run `sync.py auth google`.

Tokens land in `~/.config/ttgtsync/secrets.json` (mode 600) and refresh automatically. Each
`auth` uses a random `state` (callbacks that don't match are rejected) and Google also uses PKCE.

### Removing the 7-day refresh-token expiry

While the OAuth app is in **Testing**, Google expires refresh tokens after 7 days. Publishing the
app requires an ownership-verified domain — it does not have to relate to this project, and a
subdomain you control is fine.

1. **Verify the domain** in [Search Console](https://search.google.com/search-console): add a
   *Domain* property and publish the DNS TXT record it gives you. Use the same Google account that
   is Owner/Editor on the Cloud project.
2. **Host a privacy policy** on it (e.g. `https://yourdomain.com/privacy`). `docs/privacy.html` in
   this repo is a ready-to-use page — replace `CONTACT_EMAIL`.
3. **Google Auth Platform → Branding**: set *Authorized domains*, *Privacy policy URL*, and an
   *Application home page* — publishing stays disabled until all three exist.
4. **Audience → Publish app.** *Verification status: Not submitted* is fine for personal use;
   consent shows an "unverified app" warning you dismiss via *Advanced → continue*.
5. **Re-run `sync.py auth google`** once so the stored refresh token is issued under the published
   status.
6. **Set `"google": {"oauth_app_status": "published"}`** in `config.json` so `status` and the run
   log stop warning about the 7-day expiry.

Publishing status, not verification approval, is what stops the 7-day expiry.

---

## Running

```bash
python3 sync.py run            # one reconciliation (what the timer does)
python3 sync.py --dry-run run  # log intended writes, change nothing
python3 sync.py status         # last runs, list pairs, token ages
```

The systemd timer runs every 3 minutes with a 30-second jitter. Each run takes a `flock` on
`<state>.lock`; a run that finds the lock held exits immediately with `{"status": "busy"}`, so
overlapping ticks (or a manual run against the timer) can never race on the same state database.

`status` also reports token age and sets `reauth_soon: true` before a credential is due to expire,
so a broken token is visible instead of silent.

---

## Configuration

See `config.example.json`. Notable keys:

| Key | Default | Meaning |
|---|---|---|
| `google.calendar.id` | `primary` | calendar that receives timed blocks |
| `google.calendar.timezone` | `Europe/Berlin` | IANA zone; an unknown name is a config error |
| `google.oauth_app_status` | `testing` | `testing` warns before the 7-day token expiry; `published` doesn't |
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

`Inbox` syncs like any other list — TickTick exposes it at project id `inbox`, even though it never
appears in the project listing.

Use `lists.exclude` on your first run if you have dozens of lists. A `--dry-run` shows exactly
which Google task lists and tasks would be created before anything is written.

---

## Why timed tasks become calendar events

Google's own Tasks API reference is blunt about it:

> "Only date information is recorded; the time portion of the timestamp is discarded when setting
> this field. It isn't possible to read or write the time that a task is scheduled for using the
> API."

Calendar renders the Tasks layer as **all-day** entries. So a TickTick task at 17:30 can never be a
17:30 block through Google Tasks — no bridge, paid or free, changes that.

Timed tasks (due date + `isAllDay: false`) are therefore written as Calendar events with real
`dateTime` values and an `end` computed from your duration tag. Untimed tasks still go to Google
Tasks, so you keep the checkbox and the roll-up list.

---

## How it stays loop-free

1. **Mapping table, never title matching.** Every task maps to exactly one Google object in SQLite
   (`~/.local/state/ttgtsync/state.db`).
2. **Post-write snapshots.** After any write, the snapshot stored is what the remote now reports,
   so our own write never reads back as a user edit.
3. **Durable markers.** Google Tasks notes end with `[tt:<id>]`; events carry
   `extendedProperties.private.tt_id`. A rebuilt database re-adopts existing pairs instead of
   duplicating them.
4. **Tombstones.** Deleted blocks and completed tasks stay dead until the TickTick side actually
   changes.

Conflicts resolve last-writer-wins (`conflicts: newest`), tie goes to TickTick.

---

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

69 tests, no network. They cover duration parsing, config validation, OAuth state/PKCE,
secrets-file permissions, retry safety (a `POST` is never retried after an ambiguous failure),
domain classification, both mapping directions, echo stability (encode → decode → encode must not
drift), master-vs-occurrence selection, conflict resolution, the tombstone state machine, and the
two-strike delete sweep. CI runs them on Python 3.11–3.15, plus `ruff`.

---

## Limitations

- **Timed tasks have no Google Tasks checkbox; untimed tasks have no time.** That's the Google Tasks
  API limit, not a setting.
- **Recurring tasks** become one Calendar event carrying TickTick's RRULE verbatim. Editing a single
  occurrence in Google is ignored (it cannot be expressed in TickTick); series-level edits and
  completion roll-forward do sync. Set `recurring: "skip"` to leave them out entirely.
- **Untimed recurring tasks** mirror as one Google Task whose due date follows the current
  occurrence — Google Tasks cannot repeat.
- **TickTick hides completed tasks from its API**, so "completed" and "deleted" look identical.
  `on_disappear: "complete"` marks the Google side done rather than deleting it.
- Duration is stored in tags; resizing a block rewrites the tag (`#45min`).
- Sync latency is the timer interval, not instant.
- `on_delete: mirror` really deletes. Use `ignore` while you are still trusting it.
- Subtasks, attachments and checklists are flattened into notes.

---

## Troubleshooting

```bash
ssh your-server 'journalctl --user -u ttgtsync.service -n 50 --no-pager'
ssh your-server 'cd ~/ttgtsync && python3 sync.py status'
ssh your-server 'systemctl --user list-timers ttgtsync.timer'
```

`401`/`invalid_grant` → re-run the matching `auth` command. `403` on Calendar → the OAuth grant is
missing `calendar.events`; re-authorize Google. `{"status": "busy"}` → another run held the lock;
nothing to fix.

---

## License

MIT.
