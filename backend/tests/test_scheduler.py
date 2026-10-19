from datetime import datetime, timezone

from app.database import connect, init_db, now
from app.main import create_task, create_workflow
from app.schemas import TaskIn, WorkflowIn
from app.scheduler import next_occurrence, process_due


def configure(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "database", str(tmp_path / "scheduler.db"))
    init_db()


def test_daily_reminder_persists_once_and_advances(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    task = create_task(TaskIn(title="Review notes", reminder_at="2026-10-19T10:00:00+00:00", recurrence="daily"))
    due = datetime(2026, 10, 19, 10, 1, tzinfo=timezone.utc)
    assert process_due(due) == 1
    assert process_due(due) == 0
    with connect() as db:
        saved = db.execute("SELECT reminder_at FROM tasks WHERE id=?", (task["id"],)).fetchone()
        notices = db.execute("SELECT COUNT(*) n FROM notifications WHERE task_id=?", (task["id"],)).fetchone()["n"]
    assert saved["reminder_at"].startswith("2026-10-20T10:00:00")
    assert notices == 1


def test_weekday_recurrence_skips_weekend_and_respects_dst():
    monday = next_occurrence("2026-10-23T13:30:00+00:00", "weekdays", "America/New_York")
    assert monday == "2026-10-26T13:30:00+00:00"
    after_dst = next_occurrence("2026-10-31T13:30:00+00:00", "daily", "America/New_York")
    assert after_dst == "2026-11-01T14:30:00+00:00"


def test_overdue_workflow_schedule_is_claimed_once_and_persisted(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    workflow = create_workflow(WorkflowIn(name="Daily task", nodes=[{"id": "one", "type": "transform"}], schedule_at="2026-10-19T10:00:00+00:00", schedule_recurrence="daily", schedule_timezone="UTC"))
    due = datetime(2026, 10, 19, 10, 1, tzinfo=timezone.utc)
    assert process_due(due) == 1
    assert process_due(due) == 0
    with connect() as db:
        run = db.execute("SELECT status,trigger_type FROM runs WHERE workflow_id=?", (workflow["id"],)).fetchone()
        schedule = db.execute("SELECT next_run_at FROM workflow_schedules WHERE workflow_id=?", (workflow["id"],)).fetchone()
    assert tuple(run) == ("queued", "schedule")
    assert schedule["next_run_at"].startswith("2026-10-20T10:00:00")


def test_due_date_alert_is_persisted_once_without_reminder_time(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    task = create_task(TaskIn(title="Submit assignment", due_at="2026-10-19T10:00:00+00:00"))
    due = datetime(2026, 10, 19, 10, 1, tzinfo=timezone.utc)
    assert process_due(due) == 1
    assert process_due(due) == 0
    with connect() as db:
        row = db.execute("SELECT kind,body FROM notifications WHERE task_id=?", (task["id"],)).fetchone()
    assert tuple(row) == ("due", "Submit assignment")
