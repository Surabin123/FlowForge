"""Database-backed local scheduler. Due work is reconstructed on every poll."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .database import connect, now

logger = logging.getLogger("flowforge.scheduler")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def next_occurrence(value: str, recurrence: str | None, timezone_name: str) -> str | None:
    if not recurrence:
        return None
    zone = ZoneInfo(timezone_name)
    local = parse_instant(value).astimezone(zone)
    if recurrence == "daily":
        candidate = local + timedelta(days=1)
    elif recurrence == "weekly":
        candidate = local + timedelta(days=7)
    elif recurrence == "weekdays":
        candidate = local + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    else:
        raise ValueError("Recurrence must be daily, weekdays, weekly, or blank")
    return candidate.astimezone(timezone.utc).isoformat()


def process_due(now_utc: datetime | None = None) -> int:
    """Persist due reminder notifications and claim scheduled workflow occurrences once."""
    instant = now_utc or datetime.now(timezone.utc)
    created = 0
    with connect() as db:
        setting = db.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()
        user_timezone = setting["value"] if setting else "Asia/Calcutta"
        tasks = db.execute("SELECT * FROM tasks WHERE status='open'").fetchall()
        for task in tasks:
            if task["due_at"] and parse_instant(task["due_at"]) <= instant:
                due_key = f"task-due:{task['id']}:{task['due_at']}"
                inserted = db.execute("INSERT OR IGNORE INTO notifications(id,task_id,kind,title,body,occurrence_key,created_at) VALUES(?,?,?,?,?,?,?)", (due_key, task["id"], "due", "Task is due", task["title"], due_key, instant.isoformat()))
                created += inserted.rowcount
            if not task["reminder_enabled"] or not task["reminder_at"]:
                continue
            due = parse_instant(task["reminder_at"])
            if due > instant:
                continue
            key = f"task:{task['id']}:{due.isoformat()}"
            inserted = db.execute("INSERT OR IGNORE INTO notifications(id,task_id,kind,title,body,occurrence_key,created_at) VALUES(?,?,?,?,?,?,?)", (key, task["id"], "reminder", "Task reminder", task["title"], key, instant.isoformat()))
            created += inserted.rowcount
            try:
                next_at = next_occurrence(task["reminder_at"], task["recurrence"], user_timezone)
            except ValueError:
                next_at = None
                logger.exception("Invalid recurrence for task %s; disabling its reminder", task["id"])
                db.execute("INSERT INTO audit_log(event,resource_type,resource_id,details,created_at) VALUES('task.reminder_disabled_invalid_recurrence','task',?,'{}',?)", (task["id"], instant.isoformat()))
            if next_at:
                db.execute("UPDATE tasks SET reminder_at=?,updated_at=? WHERE id=?", (next_at, instant.isoformat(), task["id"]))
            else:
                db.execute("UPDATE tasks SET reminder_enabled=0,updated_at=? WHERE id=?", (instant.isoformat(), task["id"]))

        schedules = db.execute("SELECT s.*,w.name,w.definition,w.enabled workflow_enabled FROM workflow_schedules s JOIN workflows w ON w.id=s.workflow_id WHERE s.enabled=1 AND w.enabled=1").fetchall()
        for schedule in schedules:
            due = parse_instant(schedule["next_run_at"])
            if due > instant:
                continue
            occurrence_key = f"workflow:{schedule['workflow_id']}:{due.isoformat()}"
            claimed = db.execute("INSERT OR IGNORE INTO scheduled_fires(occurrence_key,kind,resource_id,fired_at) VALUES(?,?,?,?)", (occurrence_key, "workflow", schedule["workflow_id"], instant.isoformat()))
            if claimed.rowcount:
                try:
                    definition = schedule["definition"]
                    db.execute("INSERT INTO runs(id,workflow_id,workflow_name,status,trigger_type,started_at,created_at,definition) VALUES(?,?,?,?,?,?,?,?)", (occurrence_key, schedule["workflow_id"], schedule["name"], "queued", "schedule", instant.isoformat(), instant.isoformat(), json.dumps(json.loads(definition))))
                    db.execute("UPDATE workflow_schedules SET last_run_at=?,error=NULL WHERE workflow_id=?", (instant.isoformat(), schedule["workflow_id"]))
                    created += 1
                except Exception as exc:
                    logger.exception("Could not create scheduled run for workflow %s", schedule["workflow_id"])
                    db.execute("DELETE FROM scheduled_fires WHERE occurrence_key=?", (occurrence_key,))
                    db.execute("UPDATE workflow_schedules SET error='Could not create scheduled run' WHERE workflow_id=?", (schedule["workflow_id"],))
            next_at = next_occurrence(schedule["next_run_at"], schedule["recurrence"], schedule["timezone"])
            if next_at:
                db.execute("UPDATE workflow_schedules SET next_run_at=?,updated_at=? WHERE workflow_id=?", (next_at, instant.isoformat(), schedule["workflow_id"]))
            else:
                db.execute("UPDATE workflow_schedules SET enabled=0,updated_at=? WHERE workflow_id=?", (instant.isoformat(), schedule["workflow_id"]))
    return created


async def scheduler_loop(start_run):
    while True:
        try:
            with connect() as db:
                queued = db.execute("SELECT id,workflow_id,workflow_name,definition FROM runs WHERE status='queued' AND trigger_type='schedule'").fetchall()
            for run in queued:
                await start_run(run["id"], run["workflow_id"], run["workflow_name"], run["definition"])
            process_due()
            with connect() as db:
                queued = db.execute("SELECT id,workflow_id,workflow_name,definition FROM runs WHERE status='queued' AND trigger_type='schedule'").fetchall()
            for run in queued:
                await start_run(run["id"], run["workflow_id"], run["workflow_name"], run["definition"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduler poll failed; it will retry")
        await asyncio.sleep(5)
