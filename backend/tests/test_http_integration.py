import re, time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import config
from app.database import connect
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

def test_required_access_token_protects_api_but_keeps_health_public(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"auth.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    monkeypatch.setattr(config.settings,"require_auth",True)
    monkeypatch.setattr(config.settings,"access_token","test-private-token")
    with TestClient(app) as client:
        health=client.get("/api/health")
        assert health.status_code==200
        assert health.json()=={"status":"ok","database":"ok"}
        assert client.get("/api/auth/status").json()=={"required":True}
        assert client.get("/api/bootstrap").status_code==401
        assert client.post("/api/auth/login",json={"token":"wrong"}).status_code==401
        assert client.post("/api/auth/login",json={"token":"test-private-token"}).json()=={"authenticated":True}
        assert client.get("/api/bootstrap",headers={"Authorization":"Bearer test-private-token"}).status_code==200


@pytest.mark.parametrize("overrides,reason", [
    ({"require_auth":False,"access_token":"x"*40}, "FLOWFORGE_REQUIRE_AUTH"),
    ({"require_auth":True,"access_token":""}, "FLOWFORGE_ACCESS_TOKEN"),
    ({"require_auth":True,"access_token":"short"}, "at least 32"),
    ({"require_auth":True,"access_token":"x"*40,"allowed_hosts":"*"}, "FLOWFORGE_ALLOWED_HOSTS"),
    ({"require_auth":True,"access_token":"x"*40,"allowed_hosts":""}, "FLOWFORGE_ALLOWED_HOSTS"),
    ({"require_auth":True,"access_token":"x"*40,"database":"relative.db"}, "absolute"),
    ({"require_auth":True,"access_token":"x"*40,"documents":"relative/docs"}, "absolute"),
])
def test_container_rejects_unsafe_startup(tmp_path,monkeypatch,overrides,reason):
    monkeypatch.setattr(config.settings,"container_mode",True)
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"safe.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    monkeypatch.setattr(config.settings,"allowed_hosts","service.example.com")
    for name,value in overrides.items(): monkeypatch.setattr(config.settings,name,value)
    with pytest.raises(RuntimeError,match=reason):
        with TestClient(app): pass


def test_container_accepts_explicit_security_and_local_host_gate(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"container_mode",True)
    monkeypatch.setattr(config.settings,"require_auth",True)
    monkeypatch.setattr(config.settings,"access_token","x"*40)
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"safe.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    monkeypatch.setattr(config.settings,"allowed_hosts","service.example.com")
    with TestClient(app) as client:
        assert client.get("/api/bootstrap").status_code==401
        assert client.get("/api/bootstrap",headers={"Authorization":"Bearer "+"x"*40}).status_code==200
        assert client.get("/api/health",headers={"Host":"evil.example.com"}).status_code==400
        preflight=client.options("/api/bootstrap",headers={"Origin":"https://evil.example.com","Access-Control-Request-Method":"GET"})
        assert preflight.headers.get("access-control-allow-origin") is None
        allowed=client.options("/api/auth/login",headers={"Origin":"http://127.0.0.1:5173","Access-Control-Request-Method":"POST"})
        assert allowed.headers.get("access-control-allow-origin")=="http://127.0.0.1:5173"

def test_manual_run_idempotency_key_returns_existing_execution(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"idempotency.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    with TestClient(app) as client:
        workflow=client.post("/api/workflows",json={"name":"Idempotent start","nodes":[{"id":"one","name":"One","type":"transform","config":{"output":{"ok":True}}}],"edges":[]})
        wid=workflow.json()["id"]
        headers={"Idempotency-Key":"start-request-001"}
        first=client.post(f"/api/workflows/{wid}/runs",headers=headers)
        second=client.post(f"/api/workflows/{wid}/runs",headers=headers)
        assert first.status_code==second.status_code==202
        assert first.json()["id"]==second.json()["id"]
        assert second.json()["duplicate"] is True
        with connect() as db:
            row=db.execute("SELECT COUNT(*) count,request_id FROM runs WHERE workflow_id=?",(wid,)).fetchone()
            assert row["count"]==1
            assert row["request_id"]==first.headers["x-request-id"]

def test_built_frontend_is_served_by_fastapi(tmp_path,monkeypatch):
    dist=Path(__file__).resolve().parents[2]/"frontend"/"dist"
    if not (dist/"index.html").is_file(): pytest.skip("frontend production bundle has not been built")
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"static.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    with TestClient(app) as client:
        page=client.get("/")
        assert page.status_code==200
        assert "FlowForge" in page.text
        asset=re.search(r'(?:src|href)="(/assets/[^\"]+)',page.text)
        assert asset and client.get(asset.group(1)).status_code==200
        assert client.get("/api/health").json()["database"]=="ok"
        assert client.get("/api/not-found").status_code==404
