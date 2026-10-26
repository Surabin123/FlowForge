import asyncio
from app import config
from app.database import connect, init_db, now
from app.schemas import TaskIn, TaskPatch, WorkflowIn
from app.engine import execute_run
from app.main import create_task, update_task, create_workflow, get_run, approve_run

def configure_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "database", str(tmp_path/"test.db"))
    monkeypatch.setattr(config.settings, "documents", str(tmp_path/"docs"))
    config.settings.document_path.mkdir(parents=True,exist_ok=True)
    init_db()

def test_persisted_task_and_workflow_lifecycle(tmp_path,monkeypatch):
    configure_db(tmp_path,monkeypatch)
    task=create_task(TaskIn(title="Read chapter",priority="high",category="study"))
    assert task["title"]=="Read chapter"
    tid=task["id"]
    completed=update_task(tid,TaskPatch(status="completed"))
    assert completed["status"]=="completed" and completed["completed_at"]
    assert update_task(tid,TaskPatch(status="open"))["completed_at"] is None
    wf=create_workflow(WorkflowIn(name="Quick run",nodes=[{"id":"one","name":"One","type":"transform","config":{"output":{"ok":True}}}]))
    rid="test-run"
    with connect() as db:
        db.execute("INSERT INTO runs(id,workflow_id,workflow_name,status,started_at,created_at) VALUES(?,?,?,?,?,?)",(rid,wf["id"],wf["name"],"running",now(),now()))
    result=asyncio.run(execute_run(rid,wf["id"],wf["name"],wf["definition"]))
    assert result["status"]=="completed"
    assert get_run(rid)["nodes"][0]["status"]=="completed"

def test_cyclic_workflow_is_rejected(tmp_path,monkeypatch):
    configure_db(tmp_path,monkeypatch)
    from fastapi import HTTPException
    try:
        create_workflow(WorkflowIn(name="Bad",nodes=[{"id":"a"},{"id":"b"}],edges=[{"source":"a","target":"b"},{"source":"b","target":"a"}]))
    except HTTPException as exc:
        assert exc.status_code==422
    else:
        raise AssertionError("A cyclic workflow was accepted")

def test_manual_node_pauses_and_resumes_after_approval(tmp_path,monkeypatch):
    configure_db(tmp_path,monkeypatch)
    wf=create_workflow(WorkflowIn(name="Approval",nodes=[{"id":"prepare","name":"Prepare","type":"transform","config":{"output":{"ready":True}}},{"id":"confirm","name":"Confirm","type":"manual"},{"id":"finish","name":"Finish","type":"transform"}],edges=[{"source":"prepare","target":"confirm"},{"source":"confirm","target":"finish"}]))
    rid="approval-run"
    with connect() as db:
        db.execute("INSERT INTO runs(id,workflow_id,workflow_name,status,started_at,created_at,definition) VALUES(?,?,?,?,?,?,?)",(rid,wf["id"],wf["name"],"running",now(),now(),__import__('json').dumps(wf["definition"])))
    assert asyncio.run(execute_run(rid,wf["id"],wf["name"],wf["definition"]))["status"]=="paused"
    async def approve_and_wait():
        await approve_run(rid)
        await asyncio.sleep(.05)
    asyncio.run(approve_and_wait())
    result=get_run(rid)
    assert result["status"]=="completed"
    assert [n["status"] for n in result["nodes"]]==["completed","completed","completed"]

def test_dependent_node_receives_completed_parent_output(tmp_path,monkeypatch):
    configure_db(tmp_path,monkeypatch)
    wf=create_workflow(WorkflowIn(name="Dependency",nodes=[{"id":"first","name":"First","type":"transform","config":{"output":{"key":"value"}}},{"id":"second","name":"Second","type":"transform"}],edges=[{"source":"first","target":"second"}]))
    rid="dependency-run"
    with connect() as db: db.execute("INSERT INTO runs(id,workflow_id,workflow_name,status,started_at,created_at,definition) VALUES(?,?,?,?,?,?,?)",(rid,wf["id"],wf["name"],"running",now(),now(),__import__('json').dumps(wf["definition"])))
    asyncio.run(execute_run(rid,wf["id"],wf["name"],wf["definition"]))
    assert get_run(rid)["nodes"][1]["output"]=='{"first": {"key": "value"}}'

def test_invalid_node_output_schema_fails_run(tmp_path,monkeypatch):
    configure_db(tmp_path,monkeypatch)
    wf=create_workflow(WorkflowIn(name="Schema check",nodes=[{"id":"one","name":"One","type":"transform","config":{"output":{"value":1}},"output_schema":{"type":"string"}}]))
    rid="schema-run"
    with connect() as db: db.execute("INSERT INTO runs(id,workflow_id,workflow_name,status,started_at,created_at,definition) VALUES(?,?,?,?,?,?,?)",(rid,wf["id"],wf["name"],"running",now(),now(),__import__('json').dumps(wf["definition"])))
    assert asyncio.run(execute_run(rid,wf["id"],wf["name"],wf["definition"]))["status"]=="failed"
    assert get_run(rid)["nodes"][0]["status"]=="failed"

def test_startup_marks_interrupted_runs_as_failed(tmp_path,monkeypatch):
    from app.main import lifespan, app
    configure_db(tmp_path,monkeypatch)
    with connect() as db:
        db.execute("INSERT INTO runs(id,workflow_name,status,started_at,created_at) VALUES('stale','Old run','running',?,?)",(now(),now()))
        db.execute("INSERT INTO node_runs(id,run_id,node_id,node_name,status) VALUES('stale-node','stale','n1','Node','running')")
    async def check():
        async with lifespan(app):
            with connect() as db:
                run=db.execute("SELECT status,error FROM runs WHERE id='stale'").fetchone()
                node=db.execute("SELECT status,error FROM node_runs WHERE id='stale-node'").fetchone()
                assert run["status"]=="failed" and "restart" in run["error"]
                assert node["status"]=="failed" and "restart" in node["error"]
    asyncio.run(check())


def test_online_backup_restores_committed_task_in_temporary_database(tmp_path,monkeypatch):
    from shutil import copyfile
    from app.backup_database import backup_database

    configure_db(tmp_path,monkeypatch)
    task=create_task(TaskIn(title="Backup smoke task"))
    backup=backup_database(tmp_path/"snapshot.sqlite3")
    restored=tmp_path/"restored.sqlite3"
    copyfile(backup,restored)
    monkeypatch.setattr(config.settings,"database",str(restored))
    with connect() as db:
        row=db.execute("SELECT title FROM tasks WHERE id=?",(task["id"],)).fetchone()
    assert row["title"]=="Backup smoke task"
