"""SQLite state: list pairs, task mappings, run history."""

import json
import os
import sqlite3

from .util import utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS pairs (
  name            TEXT PRIMARY KEY,
  tt_project_id   TEXT NOT NULL UNIQUE,
  gt_list_id      TEXT NOT NULL UNIQUE,
  enabled         INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  tt_id           TEXT PRIMARY KEY,
  domain          TEXT NOT NULL,
  tt_project_id   TEXT,
  gt_id           TEXT UNIQUE,
  gt_list_id      TEXT,
  gt_calendar_id  TEXT,
  tt_updated      TEXT,
  gt_updated      TEXT,
  tt_snapshot     TEXT,
  gt_snapshot     TEXT,
  last_write_side TEXT,
  last_write_hash TEXT,
  last_write_at   TEXT,
  tombstone       INTEGER NOT NULL DEFAULT 0,
  tombstone_at    TEXT,
  tombstone_reason TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  started   TEXT NOT NULL,
  finished  TEXT,
  status    TEXT,
  stats     TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""


class Store:
    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # pairs -----------------------------------------------------------------
    def pairs(self, enabled_only=True):
        sql = "SELECT * FROM pairs"
        if enabled_only:
            sql += " WHERE enabled = 1"
        return [dict(row) for row in self.db.execute(sql + " ORDER BY name")]

    def pair_by_tt(self, project_id):
        row = self.db.execute("SELECT * FROM pairs WHERE tt_project_id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    def pair_by_gt(self, list_id):
        row = self.db.execute("SELECT * FROM pairs WHERE gt_list_id = ?", (list_id,)).fetchone()
        return dict(row) if row else None

    def upsert_pair(self, name, tt_project_id, gt_list_id, enabled=1):
        self.db.execute(
            """INSERT INTO pairs (name, tt_project_id, gt_list_id, enabled, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 tt_project_id = excluded.tt_project_id,
                 gt_list_id = excluded.gt_list_id""",
            (name, tt_project_id, gt_list_id, enabled, utcnow_iso()),
        )
        self.db.commit()

    # tasks ----------------------------------------------------------------
    def task(self, tt_id):
        row = self.db.execute("SELECT * FROM tasks WHERE tt_id = ?", (tt_id,)).fetchone()
        return dict(row) if row else None

    def task_by_gt(self, gt_id):
        row = self.db.execute("SELECT * FROM tasks WHERE gt_id = ?", (gt_id,)).fetchone()
        return dict(row) if row else None

    def linked_tasks(self):
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE gt_id IS NOT NULL AND tombstone = 0"
        ).fetchall()
        return [dict(row) for row in rows]

    def save_task(self, record):
        fields = [
            "tt_id", "domain", "tt_project_id", "gt_id", "gt_list_id", "gt_calendar_id",
            "tt_updated", "gt_updated", "tt_snapshot", "gt_snapshot",
            "last_write_side", "last_write_hash", "last_write_at",
            "tombstone", "tombstone_at", "tombstone_reason",
        ]
        payload = {key: record.get(key) for key in fields}
        payload["tt_snapshot"] = _dump(payload["tt_snapshot"])
        payload["gt_snapshot"] = _dump(payload["gt_snapshot"])
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{key} = excluded.{key}" for key in fields if key != "tt_id")
        self.db.execute(
            f"INSERT INTO tasks ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(tt_id) DO UPDATE SET {updates}",
            [payload[key] for key in fields],
        )
        self.db.commit()

    def clear_gt_link(self, tt_id, reason):
        self.db.execute(
            """UPDATE tasks SET gt_id = NULL, gt_list_id = NULL, gt_calendar_id = NULL,
               gt_snapshot = NULL, gt_updated = NULL,
               tombstone = 1, tombstone_at = ?, tombstone_reason = ? WHERE tt_id = ?""",
            (utcnow_iso(), reason, tt_id),
        )
        self.db.commit()

    def mark_deleted(self, tt_id, reason):
        self.db.execute(
            "UPDATE tasks SET tombstone = 1, tombstone_at = ?, tombstone_reason = ? WHERE tt_id = ?",
            (utcnow_iso(), reason, tt_id),
        )
        self.db.commit()

    def purge_tombstones(self, days=30):
        self.db.execute(
            "DELETE FROM tasks WHERE tombstone = 1 AND julianday(tombstone_at) < julianday('now', ?)",
            (f"-{int(days)} days",),
        )
        self.db.commit()

    # runs -----------------------------------------------------------------
    def start_run(self):
        cursor = self.db.execute(
            "INSERT INTO runs (started, status) VALUES (?, 'running')", (utcnow_iso(),)
        )
        self.db.commit()
        return cursor.lastrowid

    def finish_run(self, run_id, status, stats):
        self.db.execute(
            "UPDATE runs SET finished = ?, status = ?, stats = ? WHERE id = ?",
            (utcnow_iso(), status, json.dumps(stats, sort_keys=True), run_id),
        )
        self.db.commit()

    def last_runs(self, limit=5):
        rows = self.db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.db.commit()


def _dump(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def load_snapshot(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
