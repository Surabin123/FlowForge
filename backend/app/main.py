import asyncio, hmac, json, logging, mimetypes, sqlite3, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import settings
from .database import connect, init_db, now, audit, row_dict
from .schemas import TaskIn, TaskPatch, SubjectIn, SessionIn, SessionPatch, WorkflowIn, AgentIn
from .engine import validate_dag, validate_output_schemas, WorkflowError, execute_run
from .scheduler import scheduler_loop
from .observability import SafeJsonFormatter, metrics, request_id_context, outbound_client_context, outbound_request
from .automation import AutomationError, PlanProposal, PlanRequest, approve_plan, cancel_plan, get_plan, list_plans, propose_plan, revise_plan, save_plan
from .tool_registry import TOOLS
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()], force=True)
for handler in logging.getLogger().handlers:
    handler.setFormatter(SafeJsonFormatter())
logger=logging.getLogger("flowforge")
live_runs: dict[str, asyncio.Task] = {}

@asynccontextmanager
async def lifespan(app):
    if settings.require_auth and not settings.access_token:
        raise RuntimeError("FLOWFORGE_ACCESS_TOKEN is required when FLOWFORGE_REQUIRE_AUTH is enabled")
    import httpx
    client=httpx.AsyncClient(timeout=httpx.Timeout(45,connect=10),limits=httpx.Limits(max_connections=20,max_keepalive_connections=10,keepalive_expiry=30))
    app.state.outbound_client=client
    client_context=outbound_client_context.set(client)
    scheduler_task=None
    try:
        init_db(); settings.document_path.mkdir(parents=True,exist_ok=True)
        with connect() as db:
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('timezone',?)",(settings.timezone,))
            stale=db.execute("SELECT id FROM runs WHERE status='running'").fetchall()
            recovered_at=now()
            for run in stale:
                active_nodes=db.execute("SELECT node_id,attempts,attempt_started_at,attempt_details,duration_ms FROM node_runs WHERE run_id=? AND status='running'",(run["id"],)).fetchall()
                for node in active_nodes:
                    try: details=json.loads(node["attempt_details"] or "[]")
                    except (TypeError,json.JSONDecodeError): details=[]
                    elapsed=None
                    try: elapsed=max(0,round((datetime.fromisoformat(recovered_at)-datetime.fromisoformat(node["attempt_started_at"])).total_seconds()*1000)) if node["attempt_started_at"] else None
                    except (TypeError,ValueError): pass
                    details.append({"attempt":node["attempts"],"status":"interrupted","started_at":node["attempt_started_at"],"finished_at":recovered_at,"duration_ms":elapsed,"category":"interrupted","reason":"Application restart interrupted node execution; external effects require reconciliation"})
                    db.execute("UPDATE node_runs SET status='failed',finished_at=?,attempt_started_at=NULL,error='Interrupted by application restart',error_category='interrupted',failure_reason='Reconcile external effects before retrying',duration_ms=COALESCE(duration_ms,0)+?,attempt_details=? WHERE run_id=? AND node_id=?",(recovered_at,elapsed or 0,json.dumps(details,separators=(",",":")),run["id"],node["node_id"]))
                db.execute("UPDATE node_runs SET status='skipped',finished_at=?,error_category='interrupted',failure_reason='Not started before application restart' WHERE run_id=? AND status='queued'",(recovered_at,run["id"]))
                db.execute("UPDATE runs SET status='failed',finished_at=?,execution_id=NULL,error='Interrupted by application restart; inspect the run and retry if appropriate.',error_category='interrupted',failure_reason='External effects may have succeeded before process interruption; reconcile before retrying' WHERE id=? AND status='running'",(recovered_at,run["id"]))
                audit(db,"run.recovered_interrupted","run",run["id"],{"recovery":"marked_failed","active_nodes":len(active_nodes)})
        scheduler_task=asyncio.create_task(scheduler_loop(_start_scheduled_run))
        yield
    finally:
        if scheduler_task and not scheduler_task.done():
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task,return_exceptions=True)
        tasks=[task for task in live_runs.values() if not task.done()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        outbound_client_context.reset(client_context)
        await client.aclose()

app=FastAPI(title="FlowForge API",version="0.1.0",lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=settings.trusted_hosts)
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_methods=["*"],allow_headers=["*"])

@app.middleware("http")
async def request_instrumentation(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    context_token = request_id_context.set(request_id)
    client_token = outbound_client_context.set(getattr(request.app.state,"outbound_client",None))
    started = time.perf_counter()
    method = request.method
    try:
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        status_group = f"{response.status_code // 100}xx"
        metrics.increment("http.inbound.requests", method, route, status_group)
        metrics.observe("http.inbound.duration_ms", (time.perf_counter() - started) * 1000, method, route)
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        metrics.increment("http.inbound.requests", method, route, "5xx")
        metrics.observe("http.inbound.duration_ms", (time.perf_counter() - started) * 1000, method, route)
        logger.error("http.request.failed", extra={"flowforge": {"method": method, "route": route, "status": 500}})
        raise
    finally:
        outbound_client_context.reset(client_token)
        request_id_context.reset(context_token)

@app.middleware("http")
async def access_token_guard(request: Request, call_next):
    exempt = {"/api/health", "/api/auth/status", "/api/auth/login"}
    if settings.require_auth and request.url.path.startswith("/api/") and request.url.path not in exempt:
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {settings.access_token}"
        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return JSONResponse({"detail": "Access token required"}, status_code=401)
    return await call_next(request)

@app.get("/api/auth/status")
def auth_status():
    return {"required": settings.require_auth}

@app.post("/api/auth/login")
def auth_login(body: dict):
    token = body.get("token")
    if not settings.require_auth or not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), settings.access_token.encode("utf-8")):
        raise HTTPException(401, "Invalid access token")
    return {"authenticated": True}

@app.get("/api/internal/metrics")
def internal_metrics():
    return metrics.snapshot()

class ApprovalInput(BaseModel):
    proposed_hash: str = Field(min_length=64, max_length=64)


def _automation_error(exc: AutomationError):
    raise HTTPException(exc.status_code, exc.message) from exc


@app.get("/api/automation/tools")
def automation_tools():
    return [spec.public() for spec in TOOLS.values()]


@app.get("/api/automation/plans")
def automation_plans():
    return list_plans()


@app.post("/api/automation/plans", status_code=201)
async def create_automation_plan(body: PlanRequest):
    try:
        return await propose_plan(body.request)
    except AutomationError as exc:
        _automation_error(exc)


@app.get("/api/automation/plans/{plan_id}")
def automation_plan(plan_id: str):
    try:
        return get_plan(plan_id)
    except AutomationError as exc:
        _automation_error(exc)


@app.put("/api/automation/plans/{plan_id}")
def edit_automation_plan(plan_id: str, body: PlanProposal):
    try:
        return revise_plan(plan_id, body)
    except AutomationError as exc:
        _automation_error(exc)


@app.post("/api/automation/plans/{plan_id}/approve")
def approve_automation_plan(plan_id: str, body: ApprovalInput):
    try:
        return approve_plan(plan_id, body.proposed_hash)
    except AutomationError as exc:
        _automation_error(exc)


@app.post("/api/automation/plans/{plan_id}/cancel")
def cancel_automation_plan(plan_id: str):
    try:
        return cancel_plan(plan_id)
    except AutomationError as exc:
        _automation_error(exc)


@app.post("/api/automation/plans/{plan_id}/save")
def save_automation_plan(plan_id: str):
    try:
        return save_plan(plan_id)
    except AutomationError as exc:
        _automation_error(exc)


@app.post("/api/automation/plans/{plan_id}/run", status_code=202)
async def run_automation_plan(plan_id: str, request: Request):
    try:
        saved = save_plan(plan_id)
    except AutomationError as exc:
        _automation_error(exc)
    return await start_run(saved["workflow_id"], request)


@app.get("/api/automation/artifacts")
def automation_artifacts():
    with connect() as db:
        return [row_dict(row) for row in db.execute("SELECT id,title,kind,source_run_id,created_at FROM generated_artifacts ORDER BY created_at DESC LIMIT 50")]


@app.get("/api/automation/artifacts/{artifact_id}")
def automation_artifact(artifact_id: str):
    with connect() as db:
        row = db.execute("SELECT id,title,kind,content,source_run_id,source_node_id,created_at FROM generated_artifacts WHERE id=?", (artifact_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Artifact not found")
    value = row_dict(row)
    value["content"] = json.loads(value["content"])
    return value

@app.get("/api/health")
def health():
    try:
        with connect() as db: db.execute("SELECT 1")
        return {"status":"ok","database":"ok","worker":"single-process","ai_configured":bool(settings.ai_api_key and settings.ai_model),"github_configured":bool(settings.github_token)}
    except Exception as exc: raise HTTPException(503,"Database unavailable") from exc

@app.get("/api/bootstrap")
def bootstrap():
    with connect() as db:
        workflows=[_workflow(r) for r in db.execute("SELECT * FROM workflows ORDER BY updated_at DESC")]
        schedules={r["workflow_id"]:row_dict(r) for r in db.execute("SELECT * FROM workflow_schedules")}
        for wf in workflows: wf["schedule"]=schedules.get(wf["id"])
        return {"tasks":[row_dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY due_at IS NULL,due_at LIMIT 100")],"subjects":[row_dict(r) for r in db.execute("SELECT * FROM subjects ORDER BY name")],"sessions":[row_dict(r) for r in db.execute("SELECT s.*,u.name subject_name FROM study_sessions s LEFT JOIN subjects u ON u.id=s.subject_id ORDER BY starts_at LIMIT 100")],"documents":[row_dict(r) for r in db.execute("SELECT id,name,media_type,size_bytes,status,error,created_at,summary,summary_status,summary_error,summary_updated_at FROM documents ORDER BY created_at DESC LIMIT 50")],"workflows":workflows,"runs":[row_dict(r) for r in db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 30")],"agents":[_agent(r) for r in db.execute("SELECT * FROM agents ORDER BY name")],"notifications":[row_dict(r) for r in db.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 50")],"settings":{r["key"]:r["value"] for r in db.execute("SELECT * FROM settings")}}

def _workflow(r):
    if not r:return None
    d=row_dict(r); d["definition"]=json.loads(d.pop("definition")); d["enabled"]=bool(d["enabled"]); return d
def _agent(r):
    d=row_dict(r)
    for k in ("input_schema","output_schema"): d[k]=json.loads(d[k])
    return d

@app.get("/api/tasks")
def tasks(q:str="",status:str="all"):
    with connect() as db:
        sql="SELECT * FROM tasks WHERE (title LIKE ? OR description LIKE ?)"; args=[f"%{q}%",f"%{q}%"]
        if status!="all": sql+=" AND status=?"; args.append(status)
        sql+=" ORDER BY due_at IS NULL,due_at"
        return [row_dict(r) for r in db.execute(sql,args)]

@app.post("/api/tasks")
def create_task(body:TaskIn):
    if body.priority not in {"low","medium","high","urgent"}: raise HTTPException(422,"Invalid priority")
    if body.due_at: _valid_datetime(body.due_at)
    if body.reminder_at: _valid_datetime(body.reminder_at)
    tid=str(uuid.uuid4()); ts=now()
    with connect() as db:
        db.execute("INSERT INTO tasks(id,title,description,due_at,priority,category,recurrence,reminder_at,reminder_enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(tid,body.title,body.description,body.due_at,body.priority,body.category,body.recurrence,body.reminder_at,int(body.reminder_enabled),ts,ts)); audit(db,"task.created","task",tid)
        return row_dict(db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone())

def _valid_datetime(value):
    try:
        parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
        if parsed.tzinfo is None: raise ValueError("timezone offset required")
    except ValueError as exc: raise HTTPException(422,"Dates must be ISO 8601") from exc

@app.patch("/api/tasks/{tid}")
def update_task(tid:str, body:TaskPatch):
    values=body.model_dump(exclude_unset=True)
    if not values: raise HTTPException(422,"At least one task field is required")
    for field in ("due_at","reminder_at"):
        if values.get(field) is not None: _valid_datetime(values[field])
    if "title" in values and values["title"] is not None and not values["title"].strip(): raise HTTPException(422,"Title cannot be empty")
    if values.get("status")=="completed": values["completed_at"]=now()
    elif values.get("status") in {"open","cancelled"}: values["completed_at"]=None
    values["updated_at"]=now()
    with connect() as db:
        if not db.execute("SELECT 1 FROM tasks WHERE id=?",(tid,)).fetchone(): raise HTTPException(404,"Task not found")
        db.execute(f"UPDATE tasks SET {','.join(k+'=?' for k in values)} WHERE id=?",[*values.values(),tid]); audit(db,"task.updated","task",tid,values)
        return row_dict(db.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone())

@app.delete("/api/tasks/{tid}")
def delete_task(tid:str):
    with connect() as db:
        cur=db.execute("DELETE FROM tasks WHERE id=?",(tid,))
        if not cur.rowcount: raise HTTPException(404,"Task not found")
        audit(db,"task.deleted","task",tid)
    return {"deleted":True}

@app.get("/api/notifications")
def notifications():
    with connect() as db: return [row_dict(r) for r in db.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 200")]

@app.post("/api/notifications/{nid}/read")
def mark_notification_read(nid:str):
    with connect() as db:
        cur=db.execute("UPDATE notifications SET read_at=? WHERE id=?",(now(),nid))
        if not cur.rowcount: raise HTTPException(404,"Notification not found")
    return {"read":True}

@app.post("/api/study/subjects")
def create_subject(body:SubjectIn):
    sid=str(uuid.uuid4())
    with connect() as db:
        db.execute("INSERT INTO subjects(id,name,color,created_at) VALUES(?,?,?,?)",(sid,body.name,body.color,now()))
        return row_dict(db.execute("SELECT * FROM subjects WHERE id=?",(sid,)).fetchone())

@app.post("/api/study/sessions")
def create_session(body:SessionIn):
    _valid_datetime(body.starts_at); sid=str(uuid.uuid4())
    with connect() as db:
        if body.subject_id and not db.execute("SELECT 1 FROM subjects WHERE id=?",(body.subject_id,)).fetchone(): raise HTTPException(404,"Subject not found")
        db.execute("INSERT INTO study_sessions(id,subject_id,title,starts_at,duration_minutes,notes,created_at) VALUES(?,?,?,?,?,?,?)",(sid,body.subject_id,body.title,body.starts_at,body.duration_minutes,body.notes,now()))
        return row_dict(db.execute("SELECT * FROM study_sessions WHERE id=?",(sid,)).fetchone())

@app.patch("/api/study/sessions/{sid}")
def update_session(sid:str, body:SessionPatch):
    values=body.model_dump(exclude_unset=True)
    if not values: raise HTTPException(422,"At least one session field is required")
    with connect() as db:
        if not db.execute("SELECT 1 FROM study_sessions WHERE id=?",(sid,)).fetchone(): raise HTTPException(404,"Session not found")
        if values: db.execute(f"UPDATE study_sessions SET {','.join(k+'=?' for k in values)} WHERE id=?",[*values.values(),sid])
        return row_dict(db.execute("SELECT * FROM study_sessions WHERE id=?",(sid,)).fetchone())

SUPPORTED={"text/plain","text/markdown","text/csv","application/pdf"}
@app.post("/api/documents")
async def upload_document(file:UploadFile=File(...)):
    name=Path(file.filename or "document").name
    content=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(content)>settings.max_upload_mb*1024*1024: raise HTTPException(413,f"Upload exceeds {settings.max_upload_mb} MB")
    media=file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if media not in SUPPORTED: raise HTTPException(415,"Supported file types: PDF, TXT, Markdown, CSV")
    did=str(uuid.uuid4()); ext=Path(name).suffix.lower(); target=settings.document_path/(did+ext); target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(content)
    extracted=""; status="processed"; error=None
    try:
        if media=="application/pdf":
            from pypdf import PdfReader
            import io
            extracted="\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        else: extracted=content.decode("utf-8-sig")
    except Exception as exc: status="failed"; error=f"Text extraction failed: {exc}"
    with connect() as db:
        db.execute("INSERT INTO documents(id,name,media_type,size_bytes,path,extracted_text,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(did,name,media,len(content),str(target),extracted,status,error,now())); audit(db,"document.uploaded","document",did,{"size_bytes":len(content)})
        return row_dict(db.execute("SELECT id,name,media_type,size_bytes,status,error,created_at,summary,summary_status,summary_error,summary_updated_at FROM documents WHERE id=?",(did,)).fetchone())

@app.get("/api/documents/{did}/download")
def download_document(did:str):
    with connect() as db: row=db.execute("SELECT * FROM documents WHERE id=?",(did,)).fetchone()
    if not row: raise HTTPException(404,"Document not found")
    return FileResponse(row["path"],filename=row["name"],media_type=row["media_type"])

@app.get("/api/documents/{did}/text")
def document_text(did:str):
    with connect() as db: row=db.execute("SELECT extracted_text FROM documents WHERE id=?",(did,)).fetchone()
    if not row: raise HTTPException(404,"Document not found")
    return {"text":row["extracted_text"]}

@app.post("/api/documents/{did}/retry")
def retry_document_processing(did:str):
    with connect() as db: row=db.execute("SELECT * FROM documents WHERE id=?",(did,)).fetchone()
    if not row: raise HTTPException(404,"Document not found")
    path=Path(row["path"])
    if not path.is_file(): raise HTTPException(409,"Original file is missing from local storage")
    try:
        content=path.read_bytes()
        if row["media_type"]=="application/pdf":
            from pypdf import PdfReader
            import io
            extracted="\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        else: extracted=content.decode("utf-8-sig")
    except Exception as exc:
        with connect() as db: db.execute("UPDATE documents SET status='failed',error=? WHERE id=?",(f"Text extraction failed: {exc}",did))
        raise HTTPException(422,"Document text extraction failed") from exc
    with connect() as db:
        db.execute("UPDATE documents SET extracted_text=?,status='processed',error=NULL WHERE id=?",(extracted,did)); audit(db,"document.processing_retried","document",did)
        return row_dict(db.execute("SELECT id,name,media_type,size_bytes,status,error,created_at,summary,summary_status,summary_error,summary_updated_at FROM documents WHERE id=?",(did,)).fetchone())

@app.post("/api/documents/{did}/summarize")
async def summarize_document(did:str):
    if not settings.ai_api_key or not settings.ai_model: raise HTTPException(503,"Configure FLOWFORGE_AI_API_KEY and FLOWFORGE_AI_MODEL to summarize documents")
    with connect() as db: row=db.execute("SELECT * FROM documents WHERE id=?",(did,)).fetchone()
    if not row: raise HTTPException(404,"Document not found")
    source=row["extracted_text"].strip()
    if not source: raise HTTPException(422,"No text was extracted from this document")
    if len(source)>100000: raise HTTPException(413,"Document text is too long to summarize in one request (100,000 character limit)")
    with connect() as db: db.execute("UPDATE documents SET summary_status='processing',summary_error=NULL WHERE id=?",(did,))
    try:
        response=await outbound_request("POST",settings.ai_base_url.rstrip('/')+'/chat/completions',integration="ai",operation="document_summary",timeout=60,headers={"Authorization":f"Bearer {settings.ai_api_key}"},json={"model":settings.ai_model,"messages":[{"role":"system","content":"Summarize the supplied document accurately. Do not add facts that are not in it. Return a concise summary with key points."},{"role":"user","content":source[:100000]}],"temperature":0.2})
        if response.status_code>=400:
            safe_message=f"Provider returned HTTP {response.status_code}"
            raise RuntimeError(safe_message)
        payload=response.json(); summary=payload["choices"][0]["message"]["content"]
        usage=payload.get("usage")
        if isinstance(usage,dict) and isinstance(usage.get("total_tokens"),int) and usage["total_tokens"]>=0: metrics.increment("ai.tokens.reported",amount=usage["total_tokens"])
        if not isinstance(summary,str) or not summary.strip() or len(summary)>20000: raise ValueError("Provider returned an invalid summary")
    except Exception as exc:
        safe="AI provider request failed; check provider settings and logs"
        if isinstance(exc, RuntimeError): safe=str(exc)
        with connect() as db: db.execute("UPDATE documents SET summary_status='failed',summary_error=? WHERE id=?",(safe,did)); audit(db,"document.summarize_failed","document",did,{"reason":safe})
        raise HTTPException(502,safe) from exc
    with connect() as db:
        db.execute("UPDATE documents SET summary=?,summary_status='completed',summary_error=NULL,summary_updated_at=? WHERE id=?",(summary.strip(),now(),did)); audit(db,"document.summarized","document",did,{"provider_model":settings.ai_model})
        return row_dict(db.execute("SELECT id,name,summary,summary_status,summary_error,summary_updated_at FROM documents WHERE id=?",(did,)).fetchone())

@app.delete("/api/documents/{did}")
def delete_document(did:str):
    with connect() as db:
        row=db.execute("SELECT path FROM documents WHERE id=?",(did,)).fetchone()
        if not row: raise HTTPException(404,"Document not found")
        db.execute("DELETE FROM documents WHERE id=?",(did,)); audit(db,"document.deleted","document",did)
    Path(row["path"]).unlink(missing_ok=True); return {"deleted":True}

@app.post("/api/workflows")
def create_workflow(body:WorkflowIn):
    if any(node.get("type") == "tool" for node in body.nodes):
        raise HTTPException(422,"Tool workflows must be created from a reviewed automation plan")
    try: validate_dag(body.nodes,body.edges); validate_output_schemas(body.nodes)
    except WorkflowError as exc: raise HTTPException(422,str(exc)) from exc
    if body.schedule_at:
        _valid_datetime(body.schedule_at)
        try: ZoneInfo(body.schedule_timezone)
        except ZoneInfoNotFoundError as exc: raise HTTPException(422,"Unknown workflow timezone") from exc
    wid=str(uuid.uuid4()); ts=now(); definition=body.model_dump(exclude={"name","description","schedule_at","schedule_recurrence","schedule_timezone"})
    with connect() as db:
        db.execute("INSERT INTO workflows(id,name,description,definition,created_at,updated_at) VALUES(?,?,?,?,?,?)",(wid,body.name,body.description,json.dumps(definition),ts,ts))
        if body.schedule_at: db.execute("INSERT INTO workflow_schedules(workflow_id,enabled,next_run_at,recurrence,timezone,updated_at) VALUES(?,1,?,?,?,?)",(wid,body.schedule_at,body.schedule_recurrence,body.schedule_timezone,ts))
        audit(db,"workflow.created","workflow",wid)
        return _workflow(db.execute("SELECT * FROM workflows WHERE id=?",(wid,)).fetchone())

@app.put("/api/workflows/{wid}")
def replace_workflow(wid:str,body:WorkflowIn):
    if any(node.get("type") == "tool" for node in body.nodes):
        raise HTTPException(422,"Tool workflows must be created from a reviewed automation plan")
    try: validate_dag(body.nodes,body.edges); validate_output_schemas(body.nodes)
    except WorkflowError as exc: raise HTTPException(422,str(exc)) from exc
    if body.schedule_at:
        _valid_datetime(body.schedule_at)
        try: ZoneInfo(body.schedule_timezone)
        except ZoneInfoNotFoundError as exc: raise HTTPException(422,"Unknown workflow timezone") from exc
    with connect() as db:
        if not db.execute("SELECT 1 FROM workflows WHERE id=?",(wid,)).fetchone(): raise HTTPException(404,"Workflow not found")
        db.execute("UPDATE workflows SET name=?,description=?,definition=?,updated_at=? WHERE id=?",(body.name,body.description,json.dumps(body.model_dump(exclude={"name","description","schedule_at","schedule_recurrence","schedule_timezone"})),now(),wid))
        if body.schedule_at: db.execute("INSERT INTO workflow_schedules(workflow_id,enabled,next_run_at,recurrence,timezone,updated_at) VALUES(?,1,?,?,?,?) ON CONFLICT(workflow_id) DO UPDATE SET enabled=1,next_run_at=excluded.next_run_at,recurrence=excluded.recurrence,timezone=excluded.timezone,error=NULL,updated_at=excluded.updated_at",(wid,body.schedule_at,body.schedule_recurrence,body.schedule_timezone,now()))
        else: db.execute("DELETE FROM workflow_schedules WHERE workflow_id=?",(wid,))
        return _workflow(db.execute("SELECT * FROM workflows WHERE id=?",(wid,)).fetchone())

@app.patch("/api/workflows/{wid}/schedule")
def toggle_workflow_schedule(wid:str,enabled:bool):
    with connect() as db:
        cur=db.execute("UPDATE workflow_schedules SET enabled=?,updated_at=? WHERE workflow_id=?",(int(enabled),now(),wid))
        if not cur.rowcount: raise HTTPException(404,"Workflow schedule not found")
    return {"enabled":enabled}

@app.delete("/api/workflows/{wid}")
def delete_workflow(wid:str):
    with connect() as db:
        if not db.execute("SELECT 1 FROM workflows WHERE id=?",(wid,)).fetchone(): raise HTTPException(404,"Workflow not found")
        db.execute("DELETE FROM workflows WHERE id=?",(wid,)); audit(db,"workflow.deleted","workflow",wid)
    return {"deleted":True}

@app.post("/api/workflows/{wid}/duplicate",status_code=201)
def duplicate_workflow(wid:str):
    with connect() as db:
        source=db.execute("SELECT * FROM workflows WHERE id=?",(wid,)).fetchone()
        if not source: raise HTTPException(404,"Workflow not found")
        if any(node.get("type")=="tool" for node in json.loads(source["definition"]).get("nodes",[])):
            raise HTTPException(409,"Approved automations need a new reviewed plan before duplication")
        new_id=str(uuid.uuid4()); ts=now(); name=source["name"]+" (copy)"
        db.execute("INSERT INTO workflows(id,name,description,definition,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(new_id,name,source["description"],source["definition"],source["enabled"],ts,ts)); audit(db,"workflow.duplicated","workflow",new_id,{"source_id":wid})
        return _workflow(db.execute("SELECT * FROM workflows WHERE id=?",(new_id,)).fetchone())

@app.post("/api/workflows/{wid}/runs",status_code=202)
async def start_run(wid:str,request:Request):
    with connect() as db: wf=db.execute("SELECT * FROM workflows WHERE id=? AND enabled=1",(wid,)).fetchone()
    if not wf: raise HTTPException(404,"Workflow not found or disabled")
    idempotency_key=request.headers.get("Idempotency-Key")
    if idempotency_key is not None and (not idempotency_key or len(idempotency_key)>128 or not idempotency_key.isprintable()):
        raise HTTPException(400,"Idempotency-Key must be 1 to 128 printable characters")
    if idempotency_key:
        with connect() as db:
            existing=db.execute("SELECT id,status FROM runs WHERE workflow_id=? AND idempotency_key=?",(wid,idempotency_key)).fetchone()
        if existing: return {"id":existing["id"],"status":existing["status"],"duplicate":True}
    active=[t for t in live_runs.values() if not t.done()]
    if len(active)>=settings.max_parallel_runs: raise HTTPException(429,"Execution concurrency limit reached")
    definition=json.loads(wf["definition"]); validate_dag(definition["nodes"],definition.get("edges",[]))
    rid=str(uuid.uuid4())
    with connect() as db:
        try:
            db.execute("INSERT INTO runs(id,workflow_id,workflow_name,status,started_at,created_at,definition,request_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",(rid,wid,wf["name"],"running",now(),now(),json.dumps(definition),request.state.request_id,idempotency_key))
        except sqlite3.IntegrityError:
            if not idempotency_key: raise
            existing=db.execute("SELECT id,status FROM runs WHERE workflow_id=? AND idempotency_key=?",(wid,idempotency_key)).fetchone()
            if not existing: raise
            return {"id":existing["id"],"status":existing["status"],"duplicate":True}
        audit(db,"run.started","run",rid,{"workflow_id":wid})
    live_runs[rid]=asyncio.create_task(execute_run(rid,wid,wf["name"],definition))
    return {"id":rid,"status":"running"}

async def _start_scheduled_run(rid,wid,name,definition_json):
    if len([t for t in live_runs.values() if not t.done()])>=settings.max_parallel_runs:
        logger.warning("Scheduled run %s remains queued: concurrency limit reached",rid)
        return
    definition=json.loads(definition_json)
    with connect() as db:
        row=db.execute("SELECT status FROM runs WHERE id=?",(rid,)).fetchone()
        if not row or row["status"]!="queued": return
        claimed=db.execute("UPDATE runs SET status='running' WHERE id=? AND status='queued'",(rid,))
        if not claimed.rowcount: return
    live_runs[rid]=asyncio.create_task(execute_run(rid,wid,name,definition))

@app.post("/api/runs/{rid}/approve",status_code=202)
async def approve_run(rid:str):
    with connect() as db:
        run=db.execute("SELECT * FROM runs WHERE id=?",(rid,)).fetchone()
        if not run: raise HTTPException(404,"Run not found")
        if run["status"]!="paused": raise HTTPException(409,"Run is not waiting for approval")
        waiting=db.execute("SELECT node_id FROM node_runs WHERE run_id=? AND status='paused'",(rid,)).fetchall()
        if not waiting: raise HTTPException(409,"No approval step is waiting")
        for item in waiting: db.execute("UPDATE node_runs SET status='completed',finished_at=?,output=?,error=NULL,error_category=NULL,failure_reason=NULL WHERE run_id=? AND node_id=?",(now(),json.dumps({"approved":True}),rid,item["node_id"]))
        definition=json.loads(run["definition"])
        claimed=db.execute("UPDATE runs SET status='running',execution_id=NULL,error=NULL,error_category=NULL,failure_reason=NULL WHERE id=? AND status='paused'",(rid,))
        if not claimed.rowcount: raise HTTPException(409,"Run is no longer waiting for approval")
        audit(db,"run.approved","run",rid,{"nodes":[r["node_id"] for r in waiting]})
    live_runs[rid]=asyncio.create_task(execute_run(rid,run["workflow_id"],run["workflow_name"],definition))
    return {"id":rid,"status":"running"}

@app.get("/api/runs/{rid}")
def get_run(rid:str):
    with connect() as db:
        run=row_dict(db.execute("SELECT * FROM runs WHERE id=?",(rid,)).fetchone())
        if not run: raise HTTPException(404,"Run not found")
        run["logs"]=json.loads(run["logs"]); run["nodes"]=[row_dict(r) for r in db.execute("SELECT * FROM node_runs WHERE run_id=? ORDER BY rowid",(rid,))]
        for n in run["nodes"]: n["logs"]=json.loads(n["logs"])
        for n in run["nodes"]: n["attempt_details"]=json.loads(n.get("attempt_details") or "[]")
        return run

@app.post("/api/runs/{rid}/cancel")
async def cancel_run(rid:str):
    task=live_runs.get(rid)
    with connect() as db:
        run=db.execute("SELECT status FROM runs WHERE id=?",(rid,)).fetchone()
        if not run: raise HTTPException(404,"Run not found")
        if run["status"] not in {"running","paused"}: raise HTTPException(409,"Run is no longer active")
        changed=db.execute("UPDATE runs SET status='cancelled',finished_at=?,error_category='cancelled',failure_reason='Cancelled by the user',error='Cancelled by the user' WHERE id=? AND status IN ('running','paused')",(now(),rid))
        if not changed.rowcount: raise HTTPException(409,"Run is no longer active")
        db.execute("UPDATE node_runs SET status='cancelled',finished_at=?,error_category='cancelled',failure_reason='Run cancelled by the user' WHERE run_id=? AND status IN ('queued','running','paused')",(now(),rid)); audit(db,"run.cancelled","run",rid)
    if task and not task.done(): task.cancel()
    return {"id":rid,"status":"cancelled"}

@app.post("/api/runs/{rid}/retry",status_code=202)
async def retry_run(rid:str,request:Request):
    with connect() as db:
        old=db.execute("SELECT * FROM runs WHERE id=?",(rid,)).fetchone()
        if not old: raise HTTPException(404,"Run not found")
        if old["status"] not in {"failed","cancelled"}: raise HTTPException(409,"Only failed or cancelled runs can be retried")
        old_definition=json.loads(old["definition"])
        for node in old_definition.get("nodes",[]):
            if node.get("type") != "tool": continue
            spec=TOOLS.get((node.get("config") or {}).get("tool_id"))
            if not spec or spec.side_effect == "none": continue
            prior=db.execute("SELECT attempts FROM node_runs WHERE run_id=? AND node_id=?",(rid,node.get("id"))).fetchone()
            if prior and prior["attempts"]:
                raise HTTPException(409,"This run attempted an external or writing action; inspect and reconcile its outcome before creating a new execution")
        if not old["workflow_id"]: raise HTTPException(409,"Original workflow was deleted")
        wf=db.execute("SELECT * FROM workflows WHERE id=?",(old["workflow_id"],)).fetchone()
        if not wf: raise HTTPException(409,"Original workflow was deleted")
    return await start_run(wf["id"],request)

@app.post("/api/agents")
def create_agent(body:AgentIn):
    try:
        from jsonschema import Draft202012Validator, SchemaError
        if body.input_schema: Draft202012Validator.check_schema(body.input_schema)
        if body.output_schema: Draft202012Validator.check_schema(body.output_schema)
    except SchemaError as exc: raise HTTPException(422,f"Invalid agent schema: {exc.message}") from exc
    aid=str(uuid.uuid4()); ts=now()
    with connect() as db:
        db.execute("INSERT INTO agents(id,name,purpose,instructions,model,input_schema,output_schema,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(aid,body.name,body.purpose,body.instructions,body.model,json.dumps(body.input_schema),json.dumps(body.output_schema),ts,ts)); return _agent(db.execute("SELECT * FROM agents WHERE id=?",(aid,)).fetchone())

@app.delete("/api/agents/{aid}")
def delete_agent(aid:str):
    with connect() as db:
        cur=db.execute("DELETE FROM agents WHERE id=?",(aid,))
        if not cur.rowcount: raise HTTPException(404,"Agent not found")
    return {"deleted":True}

@app.get("/api/search")
def search(q:str=Query(min_length=1,max_length=120)):
    like=f"%{q}%"
    with connect() as db:
        return {"tasks":[row_dict(r) for r in db.execute("SELECT id,title,status,due_at FROM tasks WHERE title LIKE ? OR description LIKE ? LIMIT 10",(like,like))],"documents":[row_dict(r) for r in db.execute("SELECT id,name,status FROM documents WHERE name LIKE ? OR extracted_text LIKE ? LIMIT 10",(like,like))],"workflows":[row_dict(r) for r in db.execute("SELECT id,name FROM workflows WHERE name LIKE ? OR description LIKE ? LIMIT 10",(like,like))]}

@app.get("/api/settings")
def get_settings():
    with connect() as db: return {"preferences":{r["key"]:r["value"] for r in db.execute("SELECT * FROM settings")},"integrations":{"ai_configured":bool(settings.ai_api_key and settings.ai_model),"ai_model":settings.ai_model or None,"github_configured":bool(settings.github_token),"github_owner":settings.github_owner or None},"limits":{"max_upload_mb":settings.max_upload_mb,"max_parallel_nodes":settings.max_parallel_nodes}}

@app.put("/api/settings/{key}")
def update_setting(key:str,body:dict):
    if key not in {"timezone","notifications"}: raise HTTPException(422,"Only timezone and notification preferences can be changed here")
    value=body.get("value")
    if not isinstance(value,str) or len(value)>200: raise HTTPException(422,"Value must be a string up to 200 characters")
    if key=="timezone":
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError as exc: raise HTTPException(422,"Use a valid IANA time zone") from exc
    with connect() as db: db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value)); audit(db,"setting.updated","setting",key)
    return {"key":key,"value":value}

@app.get("/api/developer/status")
async def developer_status():
    if not settings.github_token: return {"configured":False,"message":"Set FLOWFORGE_GITHUB_TOKEN and FLOWFORGE_GITHUB_OWNER to connect repositories."}
    headers={"Authorization":"Bearer "+settings.github_token,"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
    res=await outbound_request("GET",f"https://api.github.com/users/{settings.github_owner}/repos",integration="github",operation="list_repositories",timeout=10,headers=headers,params={"per_page":30,"sort":"updated"})
    if res.status_code==401: raise HTTPException(502,"GitHub rejected the configured token. Check that it is active and has repository read access.")
    if res.status_code==403 and res.headers.get("X-RateLimit-Remaining")=="0": raise HTTPException(502,"GitHub API rate limit reached. Try again after the reset time.")
    if res.status_code>=400: raise HTTPException(502,f"GitHub repository request failed (HTTP {res.status_code})")
    repos=res.json()
    with connect() as db: db.execute("INSERT INTO settings(key,value) VALUES('github_last_refresh',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(now(),))
    return {"configured":True,"last_successful_refresh":now(),"repositories":[{"full_name":x["full_name"],"private":x["private"],"default_branch":x["default_branch"],"updated_at":x["updated_at"],"html_url":x["html_url"]} for x in repos]}

@app.get("/api/developer/repositories/{owner}/{repo}/activity")
async def repository_activity(owner:str,repo:str):
    if not settings.github_token: raise HTTPException(503,"Configure GitHub credentials first")
    headers={"Authorization":"Bearer "+settings.github_token,"Accept":"application/vnd.github+json"}
    results=await asyncio.gather(outbound_request("GET",f"https://api.github.com/repos/{owner}/{repo}/pulls",integration="github",operation="list_pulls",timeout=10,headers=headers,params={"state":"open","per_page":10}),outbound_request("GET",f"https://api.github.com/repos/{owner}/{repo}/commits",integration="github",operation="list_commits",timeout=10,headers=headers,params={"per_page":10}),outbound_request("GET",f"https://api.github.com/repos/{owner}/{repo}/actions/runs",integration="github",operation="list_actions",timeout=10,headers=headers,params={"per_page":10}))
    if any(r.status_code>=400 for r in results): raise HTTPException(502,"GitHub activity request failed")
    pulls,commits,runs=[r.json() for r in results]
    return {"pull_requests":[{"number":p["number"],"title":p["title"],"state":p["state"],"html_url":p["html_url"]} for p in pulls],"commits":[{"sha":c["sha"][:8],"message":c["commit"]["message"].splitlines()[0],"html_url":c["html_url"]} for c in commits],"workflow_runs":[{"name":x["name"],"status":x["status"],"conclusion":x["conclusion"],"html_url":x["html_url"]} for x in runs.get("workflow_runs",[])]}

# In the Render Docker image, Vite's production bundle is copied to this path.
# Local development continues to use the Vite server and its /api proxy.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    assets = frontend_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_file(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = (frontend_dist / path).resolve()
        if candidate.is_relative_to(frontend_dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
