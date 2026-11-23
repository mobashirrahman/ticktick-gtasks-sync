"""Orchestration: reconcile TickTick with Google Tasks and Google Calendar."""

import json
import logging
from datetime import timedelta

from . import classify, config as config_mod, mapping, merge
from .duration import replace_duration_tag
from .gcal import GoogleCalendar
from .gtasks import GoogleTasks
from .store import Store, load_snapshot
from .ticktick import ApiError, TickTick
from .util import canonical, parse_iso, sha256_json, utcnow, utcnow_iso

log = logging.getLogger("ttgtsync")


def pick_masters(events):
    """Index events by TickTick id, never binding to an occurrence exception.

    Google copies the master's private extended properties onto every exception,
    so an edited occurrence would otherwise masquerade as the series.
    """
    chosen = {}
    for event in events:
        if event.get("recurringEventId"):
            continue
        tt_id = mapping.event_id_of(event)
        if tt_id:
            chosen[tt_id] = event
    return chosen


class Syncer:
    def __init__(self, cfg, secrets, store: Store, dry_run=False):
        self.cfg = cfg
        self.secrets = secrets
        self.store = store
        self.dry_run = dry_run
        self.tt = TickTick(cfg, secrets)
        self.gt = GoogleTasks(cfg, secrets)
        self.gcal = GoogleCalendar(cfg, secrets)
        self.calendar_id = cfg["google"]["calendar"]["id"]
        self.tz = cfg["google"]["calendar"]["timezone"]
        self.skip_recurring = cfg["recurring"] == "skip"
        self.projects = {}
        self.tt_tasks = {}
        self.ops = []
        self.run_pairs = []

    # ------------------------------------------------------------------ run
    def run(self):
        stats = {
            "pairs": 0, "created_gt": 0, "created_tt": 0, "pushed_down": 0,
            "pushed_up": 0, "created_event": 0, "updated_event": 0,
            "deleted_gt": 0, "deleted_tt": 0, "deleted_event": 0, "completed_gt": 0,
            "migrated": 0, "adopted": 0, "conflicts": 0, "skipped": 0, "errors": 0,
        }
        run_id = None if self.dry_run else self.store.start_run()
        status = "ok"
        try:
            self._warn_token_age()
            self._load_ticktick()
            self._ensure_pairs(stats)
            for pair in self.run_pairs:
                stats["pairs"] += 1
                self._sync_tasks_domain(pair, stats)
            self._sync_calendar_domain(stats)
            self._sweep(stats)
            if not self.dry_run:
                self.store.purge_tombstones(int(self.cfg["purge_tombstones_days"]))
                self.store.set_meta("last_success", utcnow_iso())
        except Exception as exc:  # noqa: BLE001 - a failed run must be recorded, not crash the timer
            status = "error"
            stats["errors"] += 1
            stats["error"] = str(exc)
            log.exception("run failed: %s", exc)
        finally:
            if not self.dry_run:
                self.store.finish_run(run_id, status, stats)
        return status, stats

    # ------------------------------------------------------------- loading
    def _warn_token_age(self):
        """Testing-mode Google apps issue refresh tokens that die on day 7."""
        age = self.secrets.refresh_age_days("google")
        if age is not None:
            if age >= 6.5:
                log.warning(
                    "google refresh token is %.1f days old; a Testing-mode app expires it at 7 days. "
                    "Run `sync.py auth google` or publish the OAuth app.", age
                )
            else:
                log.debug("google refresh token age: %.1f days", age)
        for app in ("ticktick", "google"):
            if self.secrets.needs_reauth(app):
                log.warning(
                    "%s has no refresh token and its access token expires in %.0f days; "
                    "run `sync.py auth %s` before then.",
                    app, self.secrets.access_expiry_days(app) or 0, app,
                )

    def _load_ticktick(self):
        for project in self.tt.projects():
            self.projects[project["id"]] = project
        try:
            inbox = self.tt.project_data("inbox") or {}
            self.projects["inbox"] = {"id": "inbox", "name": "Inbox"}
            self.tt_tasks["inbox"] = inbox.get("tasks") or []
        except ApiError as exc:
            log.info("inbox is not exposed by the API: %s", exc)
        for project_id in self.projects:
            if project_id in self.tt_tasks:
                continue
            try:
                data = self.tt.project_data(project_id) or {}
            except ApiError as exc:
                log.warning("cannot read project %s: %s", project_id, exc)
                data = {}
            self.tt_tasks[project_id] = data.get("tasks") or []

    def _all_tt_ids(self):
        return {task["id"] for tasks in self.tt_tasks.values() for task in tasks}

    def _task_by_id(self, tt_id):
        for tasks in self.tt_tasks.values():
            for task in tasks:
                if task["id"] == tt_id:
                    return task
        return None

    def _ensure_pairs(self, stats):
        wanted = self._wanted_projects()
        self.run_pairs = [
            pair for pair in self.store.pairs() if pair["tt_project_id"] in wanted
        ]
        existing_tt = {pair["tt_project_id"] for pair in self.store.pairs(enabled_only=False)}
        gt_lists = self.gt.tasklists()
        by_name = {_norm(item.get("title")): item for item in gt_lists}
        for project_id, project in wanted.items():
            if project_id in existing_tt:
                continue
            name = (project.get("name") or project_id).strip()
            match = by_name.get(_norm(name))
            if match is None:
                self._op("create_tasklist", name=name, tt_project_id=project_id)
                if self.dry_run:
                    continue
                match = self.gt.create_tasklist(name)
                by_name[_norm(name)] = match
            if self.dry_run:
                self.run_pairs.append({
                    "name": name, "tt_project_id": project_id,
                    "gt_list_id": match["id"], "enabled": 1,
                })
                continue
            self.store.upsert_pair(name, project_id, match["id"])
            self.run_pairs.append(self.store.pair_by_tt(project_id))
            log.info("paired list %r -> %s", name, match["id"])

    def _wanted_projects(self):
        """Every project including Inbox; `exclude` skips, `include` overrides exclude."""
        include = {str(x) for x in self.cfg["lists"]["include"]}
        exclude = {str(x) for x in self.cfg["lists"]["exclude"]}
        wanted = {}
        for project_id, project in self.projects.items():
            name = (project.get("name") or project_id).strip()
            if project_id in include or name in include:
                wanted[project_id] = project
                continue
            if project_id in exclude or name in exclude:
                continue
            wanted[project_id] = project
        return wanted

    # --------------------------------------------------------------- tasks
    def _sync_tasks_domain(self, pair, stats):
        project_id = pair["tt_project_id"]
        list_id = pair["gt_list_id"]
        include_undated = bool(self.cfg["no_date"]["include"])
        tt_tasks = [
            task for task in self.tt_tasks.get(project_id, [])
            if not classify.should_skip(task, self.skip_recurring)
            and classify.domain_of(task, include_undated) == classify.TASKS
        ]
        gt_tasks = [task for task in self.gt.tasks(list_id) if not task.get("deleted")]
        gt_by_id = {task["id"]: task for task in gt_tasks}

        for task in tt_tasks:
            self._sync_untimed_task(task, pair, gt_by_id, stats)

        for gt_task in gt_tasks:
            if self.store.task_by_gt(gt_task["id"]):
                continue
            marker = mapping.marker_id(gt_task.get("notes"))
            row = self.store.task(marker) if marker else None
            if row and not row.get("tombstone"):
                self._save(row, gt_id=gt_task["id"], gt_list_id=list_id,
                           gt_updated=gt_task.get("updated"),
                           gt_snapshot=mapping.gt_task_view(gt_task))
                stats["adopted"] += 1
                continue
            self._create_tt_from_task(gt_task, pair, stats)

    def _sync_untimed_task(self, task, pair, gt_by_id, stats):
        tt_id = task["id"]
        project_id = pair["tt_project_id"]
        list_id = pair["gt_list_id"]
        current = mapping.tt_task_view(task)
        row = self.store.task(tt_id)

        if row and merge.out_of_domain(row.get("domain"), classify.TASKS):
            self._migrate(row, task, classify.TASKS, stats)
            row = self.store.task(tt_id) if not self.dry_run else row
            if row is not None:
                row["domain"] = classify.TASKS

        row, skip = self._revive_if_edited(row, current, stats)
        if skip:
            return
        gt_task = gt_by_id.get(row.get("gt_id")) if row and row.get("gt_id") else None

        if row and row.get("gt_id") and gt_task is None:
            if self._handle_remote_deletion(row, task, project_id, stats):
                return
            row = self.store.task(tt_id) if not self.dry_run else row

        if gt_task is None:
            body = mapping.gt_task_payload(task)
            if self.dry_run:
                self._op("create_gt_task", title=body["title"], list_id=list_id)
                return
            created = self.gt.insert_task(list_id, body)
            self._save(
                row, tt_id=tt_id, domain=classify.TASKS, tt_project_id=project_id,
                gt_id=created["id"], gt_list_id=list_id, gt_calendar_id=None,
                tt_updated=task.get("modifiedTime"), gt_updated=created.get("updated"),
                tt_snapshot=current, gt_snapshot=mapping.gt_task_view(created),
                last_write_side="tt", last_write_hash=sha256_json(body),
                last_write_at=utcnow_iso(), tombstone=0, tombstone_at=None,
                tombstone_reason=None,
            )
            stats["created_gt"] += 1
            log.info("created google task for %r", body["title"])
            return

        gt_current = mapping.gt_task_view(gt_task)
        action, conflict = merge.resolve(
            merge.changed(load_snapshot(row.get("tt_snapshot")) if row else None, current),
            merge.changed(load_snapshot(row.get("gt_snapshot")) if row else None, gt_current),
            self.cfg["conflicts"], task.get("modifiedTime"), gt_task.get("updated"),
        )
        if conflict:
            stats["conflicts"] += 1
            log.warning("conflict on %r: tt=%s gt=%s -> %s",
                        task.get("title"), canonical(current), canonical(gt_current), action)

        if action == merge.PUSH_DOWN:
            body = mapping.gt_task_payload(task)
            if self.dry_run:
                self._op("update_gt_task", title=body["title"])
                return
            updated = self.gt.patch_task(list_id, gt_task["id"], body, "title,notes,due,status")
            self._save(
                row, tt_id=tt_id, domain=classify.TASKS, tt_project_id=project_id,
                gt_id=gt_task["id"], gt_list_id=list_id,
                tt_updated=task.get("modifiedTime"), gt_updated=updated.get("updated"),
                tt_snapshot=current, gt_snapshot=mapping.gt_task_view(updated),
                last_write_side="tt", last_write_hash=sha256_json(body),
                last_write_at=utcnow_iso(), tombstone=0,
            )
            stats["pushed_down"] += 1
        elif action == merge.PUSH_UP:
            body = mapping.tt_from_gt(gt_task, project_id, self.tz)
            if self.dry_run:
                self._op("update_tt_task", title=body["title"])
                return
            updated = self.tt.update_task({**body, "id": tt_id})
            self._save(
                row, tt_id=tt_id, domain=classify.TASKS, tt_project_id=project_id,
                gt_id=gt_task["id"], gt_list_id=list_id,
                tt_updated=updated.get("modifiedTime"), gt_updated=gt_task.get("updated"),
                tt_snapshot=mapping.tt_task_view(updated), gt_snapshot=gt_current,
                last_write_side="gt", last_write_hash=sha256_json(body),
                last_write_at=utcnow_iso(), tombstone=0,
            )
            stats["pushed_up"] += 1
        else:
            self._save(
                row, tt_id=tt_id, domain=classify.TASKS, tt_project_id=project_id,
                gt_id=gt_task["id"], gt_list_id=list_id,
                tt_updated=task.get("modifiedTime"), gt_updated=gt_task.get("updated"),
                tt_snapshot=current, gt_snapshot=gt_current, tombstone=0,
            )
            stats["skipped"] += 1

    def _create_tt_from_task(self, gt_task, pair, stats):
        body = mapping.tt_from_gt(gt_task, pair["tt_project_id"], self.tz)
        if self.dry_run:
            self._op("create_tt_task", title=body["title"], project_id=pair["tt_project_id"])
            return
        created = self.tt.create_task(body)
        tt_id = created["id"]
        marked = mapping.with_marker(gt_task.get("notes"), tt_id)
        if marked != (gt_task.get("notes") or ""):
            try:
                self.gt.patch_task(pair["gt_list_id"], gt_task["id"], {"notes": marked}, "notes")
            except ApiError as exc:
                log.warning("could not stamp marker on google task %s: %s", gt_task["id"], exc)
        self._save(
            None, tt_id=tt_id, domain=classify.TASKS,
            tt_project_id=pair["tt_project_id"], gt_id=gt_task["id"],
            gt_list_id=pair["gt_list_id"], tt_updated=created.get("modifiedTime"),
            gt_updated=gt_task.get("updated"),
            tt_snapshot=mapping.tt_task_view(created),
            gt_snapshot=mapping.gt_task_view({**gt_task, "notes": marked}),
            last_write_side="gt", last_write_hash=sha256_json(body),
            last_write_at=utcnow_iso(),
        )
        stats["created_tt"] += 1
        log.info("created ticktick task for %r", body["title"])

    # ------------------------------------------------------------ calendar
    def _calendar_window(self):
        cal = self.cfg["google"]["calendar"]
        start = utcnow() - timedelta(days=int(cal.get("lookback_days", 30)))
        end = utcnow() + timedelta(days=int(cal.get("lookahead_days", 365)))
        return start, end

    @staticmethod
    def _iso_z(moment):
        return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _sync_calendar_domain(self, stats):
        start, end = self._calendar_window()
        self._window = (start, end)
        events = self.gcal.events(
            self.calendar_id, time_min=self._iso_z(start), time_max=self._iso_z(end),
            single_events=False,
        )
        by_tt = pick_masters(events)
        self._recover_missing_masters(by_tt)

        for project_id, tasks in self.tt_tasks.items():
            for task in tasks:
                if classify.should_skip(task, self.skip_recurring):
                    continue
                if classify.domain_of(task) != classify.CALENDAR:
                    continue
                self._sync_timed_task(task, by_tt, stats)

    def _recover_missing_masters(self, by_tt):
        """Fetch mapped events the list window missed (old recurring masters, far dates)."""
        for row in self.store.linked_tasks():
            if row["domain"] != classify.CALENDAR or not row.get("gt_id"):
                continue
            if row["tt_id"] in by_tt:
                continue
            try:
                event = self.gcal.get_event(self.calendar_id, row["gt_id"])
            except ApiError as exc:
                if exc.status in (404, 410):
                    by_tt[row["tt_id"]] = {
                        "id": row["gt_id"],
                        "status": "cancelled",
                        "extendedProperties": {"private": {"tt_id": row["tt_id"]}},
                    }
                else:
                    log.warning("cannot fetch event %s: %s", row["gt_id"], exc)
                continue
            if event:
                master_id = event.get("recurringEventId")
                if master_id:
                    try:
                        event = self.gcal.get_event(self.calendar_id, master_id)
                        if not self.dry_run:
                            self._save(row, gt_id=master_id)
                    except ApiError as exc:
                        log.warning("cannot fetch master %s: %s", master_id, exc)
                        event = None
            if event:
                by_tt[row["tt_id"]] = event

    def _sync_timed_task(self, task, by_tt, stats):
        tt_id = task["id"]
        project_id = task.get("projectId")
        minutes = config_mod.duration_minutes(self.cfg, task)
        desired = mapping.desired_event_view(task, minutes, self.tz)
        row = self.store.task(tt_id)

        if row and merge.out_of_domain(row.get("domain"), classify.CALENDAR):
            self._migrate(row, task, classify.CALENDAR, stats)
            row = self.store.task(tt_id) if not self.dry_run else row
            if row is not None:
                row["domain"] = classify.CALENDAR

        row, skip = self._revive_if_edited(row, desired, stats)
        if skip:
            return
        event = by_tt.get(tt_id)

        if classify.is_completed(task) and self.cfg["timed_tasks"]["on_complete"] == "delete":
            recurring = classify.is_recurring(task)
            ended = recurring and not task.get("dueDate")
            if not recurring or ended:
                if event and event.get("status") != "cancelled":
                    self._delete_event(event["id"], stats)
                self._tombstone(row, tt_id, classify.CALENDAR, project_id, desired, "completed")
                stats["skipped"] += 1
                return

        if event is not None and event.get("status") == "cancelled":
            self._handle_event_deletion(row, task, stats)
            return

        if event is None:
            payload = mapping.event_payload(
                task, minutes, self.tz, self.cfg["timed_tasks"]["transparency"]
            )
            if self.dry_run:
                self._op("create_event", summary=payload["summary"],
                         start=payload["start"]["dateTime"], minutes=minutes)
                return
            created = self.gcal.insert_event(self.calendar_id, payload)
            self._save(
                row, tt_id=tt_id, domain=classify.CALENDAR, tt_project_id=project_id,
                gt_id=created["id"], gt_list_id=None, gt_calendar_id=self.calendar_id,
                tt_updated=task.get("modifiedTime"), gt_updated=created.get("updated"),
                tt_snapshot=desired, gt_snapshot=_event_snapshot(created),
                last_write_side="tt", last_write_hash=sha256_json(payload),
                last_write_at=utcnow_iso(), tombstone=0, tombstone_at=None,
                tombstone_reason=None,
            )
            stats["created_event"] += 1
            log.info("created calendar block for %r", payload["summary"])
            return

        event_current = _event_snapshot(event)
        action, conflict = merge.resolve(
            merge.changed(load_snapshot(row.get("tt_snapshot")) if row else None, desired),
            merge.changed(load_snapshot(row.get("gt_snapshot")) if row else None, event_current),
            self.cfg["conflicts"], task.get("modifiedTime"), event.get("updated"),
        )
        if conflict:
            stats["conflicts"] += 1
            log.warning("conflict on event %r: tt=%s gt=%s -> %s",
                        task.get("title"), canonical(desired), canonical(event_current), action)

        if action == merge.PUSH_DOWN:
            payload = mapping.event_payload(
                task, minutes, self.tz, self.cfg["timed_tasks"]["transparency"]
            )
            mask = "summary,description,start,end,transparency"
            if desired.get("rrule") != event_current.get("rrule"):
                mask += ",recurrence"
                if not payload.get("recurrence"):
                    payload["recurrence"] = []
            if self.dry_run:
                self._op("update_event", summary=payload["summary"], mask=mask)
                return
            updated = self.gcal.patch_event(self.calendar_id, event["id"], payload, mask)
            self._save(
                row, tt_id=tt_id, domain=classify.CALENDAR, tt_project_id=project_id,
                gt_id=event["id"], gt_calendar_id=self.calendar_id,
                tt_updated=task.get("modifiedTime"), gt_updated=updated.get("updated"),
                tt_snapshot=desired, gt_snapshot=_event_snapshot(updated),
                last_write_side="tt", last_write_hash=sha256_json(payload),
                last_write_at=utcnow_iso(), tombstone=0,
            )
            stats["updated_event"] += 1
        elif action == merge.PUSH_UP:
            patch = mapping.tt_patch_from_event(event, task, self.tz)
            extra_minutes = patch.pop("_duration_minutes", None)
            if extra_minutes:
                patch["tags"] = replace_duration_tag(
                    task.get("tags") or [], extra_minutes
                )
            if self.dry_run:
                self._op("update_tt_from_event", title=task.get("title"), patch=patch)
                return
            updated = self.tt.update_task({**patch, "id": tt_id})
            self._save(
                row, tt_id=tt_id, domain=classify.CALENDAR, tt_project_id=project_id,
                gt_id=event["id"], gt_calendar_id=self.calendar_id,
                tt_updated=updated.get("modifiedTime"), gt_updated=event.get("updated"),
                tt_snapshot=mapping.desired_event_view(
                    updated, config_mod.duration_minutes(self.cfg, updated), self.tz
                ),
                gt_snapshot=event_current,
                last_write_side="gt", last_write_hash=sha256_json(patch),
                last_write_at=utcnow_iso(), tombstone=0,
            )
            stats["pushed_up"] += 1
        else:
            self._save(
                row, tt_id=tt_id, domain=classify.CALENDAR, tt_project_id=project_id,
                gt_id=event["id"], gt_calendar_id=self.calendar_id,
                tt_updated=task.get("modifiedTime"), gt_updated=event.get("updated"),
                tt_snapshot=desired, gt_snapshot=event_current, tombstone=0,
            )
            stats["skipped"] += 1

    def _handle_event_deletion(self, row, task, stats):
        policy = self.cfg["timed_tasks"]["on_event_delete"]
        tt_id = task["id"]
        if self.dry_run:
            self._op("handle_event_deletion", title=task.get("title"), policy=policy)
            return
        if policy == "delete":
            if task.get("projectId"):
                self.tt.delete_task(task["projectId"], tt_id)
            stats["deleted_tt"] += 1
            self._tombstone(row, tt_id, classify.CALENDAR, task.get("projectId"),
                            None, "event_deleted_task_removed")
            return
        if policy == "untime":
            moment = mapping.tt_due_local(task, self.tz)
            day = moment.date().isoformat() if moment else None
            patch = {"id": tt_id, "projectId": task.get("projectId"), "isAllDay": True}
            if day:
                patch["dueDate"] = f"{day}T00:00:00+0100" if self.tz == "Europe/Berlin" else f"{day}T00:00:00+0000"
                patch["timeZone"] = self.tz
            self.tt.update_task(patch)
            stats["pushed_up"] += 1
        self._tombstone(row, tt_id, classify.CALENDAR, task.get("projectId"), None,
                        "event_deleted")

    # -------------------------------------------------------- bookkeeping
    def _revive_if_edited(self, row, current_snapshot, stats):
        """Return (row, skip). Tombstoned rows stay dead until the task is edited."""
        if not row or not row.get("tombstone") or row.get("gt_id"):
            return row, False
        previous = load_snapshot(row.get("tt_snapshot"))
        if previous is not None and not merge.changed(previous, current_snapshot):
            stats["skipped"] += 1
            return row, True
        return self._save(row, tombstone=0, tombstone_at=None, tombstone_reason=None), False

    def _handle_remote_deletion(self, row, task, project_id, stats):
        """Google side vanished: mirror the delete, or restore if it was edited there."""
        if self.cfg["on_delete"] == "ignore":
            stats["skipped"] += 1
            return False
        if self.cfg["on_delete"] == "resurrect" or row.get("gt_snapshot") is None:
            return False
        if self.dry_run:
            self._op("mirror_delete_to_ticktick", title=task.get("title"))
            return True
        stats["deleted_tt"] += 1
        self.tt.delete_task(project_id, task["id"])
        self._tombstone(row, task["id"], classify.TASKS, project_id, None, "remote_deleted")
        log.info("mirrored google deletion to ticktick for %r", task.get("title"))
        return True

    def _migrate(self, row, task, domain, stats):
        tt_id = task["id"]
        previous = row.get("domain")
        if self.dry_run:
            self._op("migrate", title=task.get("title"), src=previous, dst=domain)
            return
        try:
            if previous == classify.CALENDAR and row.get("gt_id"):
                self.gcal.delete_event(self.calendar_id, row["gt_id"])
            elif previous == classify.TASKS and row.get("gt_id") and row.get("gt_list_id"):
                self.gt.delete_task(row["gt_list_id"], row["gt_id"])
        except ApiError as exc:
            if exc.status not in (404, 410):
                log.warning("migration cleanup failed for %s: %s", tt_id, exc)
        self.store.clear_gt_link(tt_id, f"migrate:{previous}->{domain}")
        fresh = self.store.task(tt_id)
        if fresh:
            self._save(fresh, tombstone=0, tombstone_at=None, tombstone_reason=None)
        stats["migrated"] += 1
        log.info("migrated %r from %s to %s", task.get("title"), previous, domain)

    def _tombstone(self, row, tt_id, domain, project_id, snapshot, reason):
        if self.dry_run:
            return
        self._save(
            row, tt_id=tt_id, domain=domain, tt_project_id=project_id,
            gt_id=None, gt_list_id=None, gt_calendar_id=None, gt_snapshot=None,
            tt_snapshot=snapshot, tombstone=1, tombstone_at=utcnow_iso(),
            tombstone_reason=reason,
        )

    def _sweep(self, stats):
        """Delete remote objects only after the task is absent for two consecutive runs."""
        existing = self._all_tt_ids()
        for row in self.store.linked_tasks():
            tt_id = row["tt_id"]
            if tt_id in existing:
                self.store.set_meta(f"missing:{tt_id}", "0")
                continue
            strikes = int(self.store.get_meta(f"missing:{tt_id}", "0") or 0) + 1
            log.debug("sweep: %s domain=%s missing strike=%d gt=%s",
                      tt_id, row["domain"], strikes, row.get("gt_id"))
            if self.dry_run:
                self._op("sweep_missing_tt", tt_id=tt_id, domain=row["domain"], strikes=strikes)
                continue
            self.store.set_meta(f"missing:{tt_id}", str(strikes))
            if strikes < 2:
                stats["skipped"] += 1
                continue
            try:
                if row["domain"] == classify.TASKS and row.get("gt_list_id"):
                    # TickTick hides completed tasks from project data, so a task
                    # disappearing almost always means "completed", not "deleted".
                    if self.cfg["on_disappear"] == "complete":
                        self.gt.patch_task(row["gt_list_id"], row["gt_id"],
                                           {"status": "completed"}, "status")
                        stats["completed_gt"] += 1
                    else:
                        self.gt.delete_task(row["gt_list_id"], row["gt_id"])
                        stats["deleted_gt"] += 1
                elif row["domain"] == classify.CALENDAR:
                    self.gcal.delete_event(self.calendar_id, row["gt_id"])
                    stats["deleted_event"] += 1
            except ApiError as exc:
                if exc.status not in (404, 410):
                    log.warning("sweep failed for %s: %s", tt_id, exc)
            self.store.mark_deleted(tt_id, "swept")
            self.store.set_meta(f"missing:{tt_id}", "0")

    # -------------------------------------------------------------- writes
    def _save(self, row, **changes):
        record = dict(row or {})
        record.update(changes)
        record.setdefault("tombstone", 0)
        if self.dry_run:
            return record
        self.store.save_task(record)
        return record

    def _delete_event(self, event_id, stats):
        if self.dry_run:
            self._op("delete_event", event_id=event_id)
            return
        try:
            self.gcal.delete_event(self.calendar_id, event_id)
        except ApiError as exc:
            if exc.status not in (404, 410):
                raise
        stats["deleted_event"] += 1

    def _op(self, kind, **detail):
        entry = {"kind": kind, **detail}
        self.ops.append(entry)
        log.info("dry-run %s", json.dumps(entry, default=str, sort_keys=True))


def _event_snapshot(event):
    view = mapping.event_view(event)
    view.pop("status", None)
    return view


def _norm(value):
    return (value or "").strip().casefold()


def configured_ok(cfg):
    return bool(cfg)
