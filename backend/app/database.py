import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import settings
from .observability import metrics
import time

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', due_at TEXT, priority TEXT NOT NULL DEFAULT 'medium', category TEXT NOT NULL DEFAULT 'personal', status TEXT NOT NULL DEFAULT 'open', recurrence TEXT, reminder_at TEXT, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, occurrence_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, read_at TEXT);
CREATE TABLE IF NOT EXISTS subjects (id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#52796f', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS study_sessions (id TEXT PRIMARY KEY, subject_id TEXT REFERENCES subjects(id) ON DELETE SET NULL, title TEXT NOT NULL, starts_at TEXT NOT NULL, duration_minutes INTEGER NOT NULL, actual_minutes INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'planned', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, name TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, path TEXT NOT NULL, extracted_text TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'processed', error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', definition TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_schedules (workflow_id TEXT PRIMARY KEY REFERENCES workflows(id) ON DELETE CASCADE, enabled INTEGER NOT NULL DEFAULT 1, next_run_at TEXT NOT NULL, recurrence TEXT, timezone TEXT NOT NULL, last_run_at TEXT, error TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scheduled_fires (occurrence_key TEXT PRIMARY KEY, kind TEXT NOT NULL, resource_id TEXT NOT NULL, fired_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL, workflow_name TEXT NOT NULL, status TEXT NOT NULL, trigger_type TEXT NOT NULL DEFAULT 'manual', started_at TEXT NOT NULL, finished_at TEXT, error TEXT, logs TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS node_runs (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, node_id TEXT NOT NULL, node_name TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT, finished_at TEXT, output TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0, logs TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', input_schema TEXT NOT NULL DEFAULT '{}', output_schema TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT, details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automation_plans (id TEXT PRIMARY KEY, request_text TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, definition TEXT NOT NULL, status TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]', schedule_at TEXT, schedule_recurrence TEXT, schedule_timezone TEXT NOT NULL, approval_hash TEXT, approved_at TEXT, workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL, provider_model TEXT, prompt_version TEXT NOT NULL, tokens_total INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automation_approvals (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES automation_plans(id) ON DELETE CASCADE, definition_hash TEXT NOT NULL, approved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generated_artifacts (id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, source_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, source_node_id TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_run_id,source_node_id));
CREATE TABLE IF NOT EXISTS tool_effects (run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, node_id TEXT NOT NULL, tool_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id,node_id));
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC); CREATE INDEX IF NOT EXISTS idx_runs_status_trigger ON runs(status,trigger_type,created_at);
CREATE INDEX IF NOT EXISTS idx_node_runs_run_status ON node_runs(run_id,status);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled_next ON workflow_schedules(enabled,next_run_at);
CREATE INDEX IF NOT EXISTS idx_automation_plans_updated ON automation_plans(updated_at DESC);
"""

def now() -> str: return datetime.now(timezone.utc).isoformat()

@contextmanager
def connect():
    started=time.perf_counter()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.db_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback(); raise
    finally:
        db.close()
        metrics.observe("database.transaction.duration_ms",(time.perf_counter()-started)*1000)

def init_db():
    with connect() as db:
        db.executescript(SCHEMA)
        task_cols={r["name"] for r in db.execute("PRAGMA table_info(tasks)")}
        if "completed_at" not in task_cols: db.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
        if "reminder_enabled" not in task_cols: db.execute("ALTER TABLE tasks ADD COLUMN reminder_enabled INTEGER NOT NULL DEFAULT 1")
        document_cols={r["name"] for r in db.execute("PRAGMA table_info(documents)")}
        for name, definition in (("summary_status", "TEXT NOT NULL DEFAULT 'not_requested'"), ("summary_error", "TEXT"), ("summary_updated_at", "TEXT")):
            if name not in document_cols: db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
        cols={r["name"] for r in db.execute("PRAGMA table_info(runs)")}
        if "definition" not in cols: db.execute("ALTER TABLE runs ADD COLUMN definition TEXT NOT NULL DEFAULT '{}' ")
        run_migrations={"request_id":"TEXT","execution_id":"TEXT","duration_ms":"INTEGER","error_category":"TEXT","failure_reason":"TEXT","idempotency_key":"TEXT"}
        for name,definition in run_migrations.items():
            if name not in cols: db.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
        node_cols={r["name"] for r in db.execute("PRAGMA table_info(node_runs)")}
        node_migrations={"duration_ms":"INTEGER","error_category":"TEXT","failure_reason":"TEXT","attempt_details":"TEXT NOT NULL DEFAULT '[]'","attempt_started_at":"TEXT"}
        for name,definition in node_migrations.items():
            if name not in node_cols: db.execute(f"ALTER TABLE node_runs ADD COLUMN {name} {definition}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_reminder_due ON tasks(status,reminder_enabled,reminder_at,due_at)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency ON runs(workflow_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
        db.execute("DELETE FROM node_runs WHERE rowid NOT IN (SELECT MAX(rowid) FROM node_runs GROUP BY run_id,node_id)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_node_runs_unique ON node_runs(run_id,node_id)")

def row_dict(row):
    return dict(row) if row else None

def audit(db, event, resource_type, resource_id=None, details=None):
    db.execute("INSERT INTO audit_log(event,resource_type,resource_id,details,created_at) VALUES(?,?,?,?,?)", (event,resource_type,resource_id,json.dumps(details or {}),now()))
