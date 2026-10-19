import time
from fastapi.testclient import TestClient
from app import config
from app.main import app

def test_http_task_and_workflow_run_lifecycle(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"http.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["database"]=="ok"
        created=client.post("/api/tasks",json={"title":"HTTP task","priority":"high"})
        assert created.status_code==200
        task_id=created.json()["id"]
        assert client.patch(f"/api/tasks/{task_id}",json={"status":"completed"}).json()["status"]=="completed"
        workflow=client.post("/api/workflows",json={"name":"HTTP workflow","nodes":[{"id":"one","name":"One","type":"transform","config":{"output":{"ok":True}}}],"edges":[]})
        assert workflow.status_code==200
        started=client.post(f"/api/workflows/{workflow.json()['id']}/runs")
        assert started.status_code==202
        for _ in range(50):
            state=client.get(f"/api/runs/{started.json()['id']}").json()
            if state["status"]!="running": break
            time.sleep(.01)
        assert state["status"]=="completed"
        assert state["nodes"][0]["output"]=='{"ok": true}'

def test_http_paused_run_can_be_cancelled(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"cancel.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    with TestClient(app) as client:
        workflow=client.post("/api/workflows",json={"name":"Approval gate","nodes":[{"id":"approval","name":"Approval","type":"manual"}],"edges":[]})
        started=client.post(f"/api/workflows/{workflow.json()['id']}/runs")
        run_id=started.json()["id"]
        for _ in range(50):
            state=client.get(f"/api/runs/{run_id}").json()
            if state["status"]=="paused": break
            time.sleep(.01)
        assert state["status"]=="paused"
        cancelled=client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.json()["status"]=="cancelled"
        assert client.get(f"/api/runs/{run_id}").json()["nodes"][0]["status"]=="cancelled"
