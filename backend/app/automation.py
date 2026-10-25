"""Natural-language plan validation, exact approval, and workflow handoff."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import settings
from .database import audit, connect, now
from .engine import WorkflowError, validate_dag
from .observability import metrics, outbound_request
from .tool_registry import TOOLS, ToolError, validate_tool_node

PROMPT_VERSION = "automation-planner-v1"
STEP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


class AutomationError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code, self.message = status_code, message


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanRequest(StrictModel):
    request: str = Field(min_length=10, max_length=3000)


class ProposedStep(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["tool", "approval"] = "tool"
    tool_id: str | None = None
    arguments: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    approval_message: str | None = Field(default=None, max_length=300)


class ProposedSchedule(StrictModel):
    starts_at: str
    recurrence: Literal["daily", "weekdays", "weekly"] | None = None
    timezone: str = "Asia/Calcutta"


class PlanProposal(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1000)
    steps: list[ProposedStep] = Field(default_factory=list, max_length=12)
    schedule: ProposedSchedule | None = None
    clarifying_questions: list[str] = Field(default_factory=list, max_length=5)


def _schedule_values(schedule: ProposedSchedule | None) -> tuple[str | None, str | None, str]:
    if not schedule:
        return None, None, settings.timezone
    try:
        instant = datetime.fromisoformat(schedule.starts_at.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("offset required")
        ZoneInfo(schedule.timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AutomationError(422, "Scheduled starts_at needs an ISO time with offset and a valid IANA timezone") from exc
    return instant.isoformat(), schedule.recurrence, schedule.timezone


def validate_proposal(proposal: PlanProposal) -> tuple[dict, tuple[str | None, str | None, str]]:
    questions = [question.strip() for question in proposal.clarifying_questions]
    if any(not question or len(question) > 300 for question in questions):
        raise AutomationError(422, "Clarifying questions must be 1 to 300 characters")
    schedule = _schedule_values(proposal.schedule)
    if questions:
        if proposal.steps:
            raise AutomationError(422, "A plan needing clarification cannot contain executable steps")
        return {"nodes": [], "edges": []}, schedule
    if not proposal.steps:
        raise AutomationError(422, "The proposed plan has no executable steps")
    ids = [step.id for step in proposal.steps]
    if len(set(ids)) != len(ids) or any(not STEP_ID.fullmatch(step_id) for step_id in ids):
        raise AutomationError(422, "Plan step IDs must be unique, short identifiers")
    nodes = []
    edges = []
    for step in proposal.steps:
        if len(set(step.depends_on)) != len(step.depends_on):
            raise AutomationError(422, "Duplicate dependencies are not allowed")
        for parent in step.depends_on:
            edges.append({"source": parent, "target": step.id})
        if step.kind == "approval":
            if step.tool_id or step.arguments:
                raise AutomationError(422, "Approval steps cannot select tools or parameters")
            nodes.append({"id": step.id, "name": step.name, "type": "manual", "config": {"message": step.approval_message or "Approve this step to continue"}})
            continue
        if not step.tool_id or step.approval_message:
            raise AutomationError(422, "Tool steps need a tool ID and cannot carry an approval message")
        try:
            config = validate_tool_node({"config": {"tool_id": step.tool_id, "arguments": step.arguments}}, set(ids), set(step.depends_on))
        except ToolError as exc:
            raise AutomationError(422, exc.reason) from exc
        spec = TOOLS[step.tool_id]
        nodes.append({"id": step.id, "name": step.name, "type": "tool", "config": config, "timeout_seconds": spec.timeout_seconds, "max_attempts": 2 if spec.retry_policy == "transient_read" else 1})
    try:
        validate_dag(nodes, edges)
    except WorkflowError as exc:
        raise AutomationError(422, str(exc)) from exc
    # Check exact static targets before presenting an executable plan.
    with connect() as db:
        for node in nodes:
            if node["type"] != "tool":
                continue
            tool_id, args = node["config"]["tool_id"], node["config"]["arguments"]
            if tool_id.startswith("documents.") and tool_id != "documents.list":
                if not db.execute("SELECT 1 FROM documents WHERE id=?", (args.get("document_id"),)).fetchone():
                    raise AutomationError(422, "The proposed document is not available")
            if tool_id == "tasks.complete" and not db.execute("SELECT 1 FROM tasks WHERE id=?", (args.get("task_id"),)).fetchone():
                raise AutomationError(422, "The proposed task is not available")
            if tool_id == "workflows.get_run" and not db.execute("SELECT 1 FROM runs WHERE id=?", (args.get("run_id"),)).fetchone():
                raise AutomationError(422, "The proposed run is not available")
    return {"nodes": nodes, "edges": edges}, schedule


def plan_payload(title: str, summary: str, definition: dict, schedule: tuple[str | None, str | None, str]) -> dict:
    return {"title": title, "summary": summary, "nodes": definition["nodes"], "edges": definition["edges"], "schedule_at": schedule[0], "schedule_recurrence": schedule[1], "schedule_timezone": schedule[2]}


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _render(row) -> dict:
    value = dict(row)
    value["definition"] = json.loads(value["definition"])
    value["questions"] = json.loads(value.pop("questions"))
    value["schedule"] = {"starts_at": value["schedule_at"], "recurrence": value["schedule_recurrence"], "timezone": value["schedule_timezone"]} if value["schedule_at"] else None
    value["proposed_hash"] = None if value["questions"] else fingerprint(plan_payload(value["title"], value["summary"], value["definition"], (value["schedule_at"], value["schedule_recurrence"], value["schedule_timezone"])))
    value["permissions"] = [TOOLS[node["config"]["tool_id"]].public() for node in value["definition"]["nodes"] if node["type"] == "tool"]
    return value


def get_plan(plan_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        raise AutomationError(404, "Automation plan not found")
    return _render(row)


def list_plans() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM automation_plans ORDER BY updated_at DESC LIMIT 30").fetchall()
    return [_render(row) for row in rows]


def _store_plan(request_text: str, proposal: PlanProposal, *, model: str | None, tokens: int | None) -> dict:
    definition, schedule = validate_proposal(proposal)
    plan_id, timestamp = str(uuid.uuid4()), now()
    status = "needs_clarification" if proposal.clarifying_questions else "awaiting_approval"
    with connect() as db:
        db.execute("INSERT INTO automation_plans(id,request_text,title,summary,definition,status,questions,schedule_at,schedule_recurrence,schedule_timezone,provider_model,prompt_version,tokens_total,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (plan_id, request_text, proposal.title, proposal.summary, json.dumps(definition), status, json.dumps(proposal.clarifying_questions), *schedule, model, PROMPT_VERSION, tokens, timestamp, timestamp))
        audit(db, "automation.plan_created", "automation_plan", plan_id, {"status": status, "prompt_version": PROMPT_VERSION})
        return _render(db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone())


async def propose_plan(request_text: str) -> dict:
    if not settings.ai_api_key or not settings.ai_model:
        raise AutomationError(503, "Configure FLOWFORGE_AI_API_KEY and FLOWFORGE_AI_MODEL to interpret natural-language requests")
    import httpx
    catalog = [{"id": spec.identifier, "version": spec.version, "description": spec.description, "input_schema": spec.input_model.model_json_schema(), "permission": spec.permission, "side_effect": spec.side_effect} for spec in TOOLS.values()]
    system = (
        f"You are FlowForge's plan proposer ({PROMPT_VERSION}). Return one JSON object only with title, summary, steps, schedule, clarifying_questions. "
        "A step has id, name, kind='tool' or 'approval', tool_id, arguments, depends_on, approval_message. "
        "Use only registered tool IDs and exact input schemas. Maximum 12 steps. Dependencies must name step IDs. "
        "For a daily plan use tasks.list_open, plans.daily, artifacts.save in that order. For a weekly review use tasks.list_completed, plans.weekly, artifacts.save. "
        "Schedules need an explicit starts_at ISO timestamp with timezone offset, recurrence (daily/weekdays/weekly/null), and IANA timezone. "
        "If the request lacks a time, target ID, tool, permission, or detail needed for safe execution, return 1-5 concise clarifying_questions and no steps. "
        "Do not invent services, credentials, data, approvals, or completed actions. External website, email, calendar, shell, and unrestricted HTTP tools are unavailable. "
        "The plan is a proposal only; it must not claim execution. Tool catalog: " + json.dumps(catalog, separators=(",", ":"))
    )
    if len(system) + len(request_text) > settings.max_ai_input_chars:
        raise AutomationError(413, "Planning request exceeds the configured input limit")
    try:
        response = await outbound_request("POST", settings.ai_base_url.rstrip("/") + "/chat/completions", integration="ai", operation="automation_plan", timeout=httpx.Timeout(45, connect=10), headers={"Authorization": "Bearer " + settings.ai_api_key}, json={"model": settings.ai_model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": request_text}]})
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise AutomationError(502, "The AI provider did not respond to the planning request") from exc
    if response.status_code >= 400:
        raise AutomationError(502, f"AI provider returned HTTP {response.status_code} while planning")
    if len(response.content) > 100_000:
        raise AutomationError(502, "The AI provider returned a plan larger than the allowed response limit")
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        proposal = PlanProposal.model_validate_json(content)
    except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
        raise AutomationError(502, "The AI provider returned an invalid plan") from exc
    usage = payload.get("usage") if isinstance(payload, dict) else None
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    if not isinstance(tokens, int) or tokens < 0:
        tokens = None
    if tokens is not None:
        metrics.increment("ai.tokens.reported", amount=tokens)
    return _store_plan(request_text, proposal, model=settings.ai_model, tokens=tokens)


def revise_plan(plan_id: str, proposal: PlanProposal) -> dict:
    definition, schedule = validate_proposal(proposal)
    with connect() as db:
        row = db.execute("SELECT status,workflow_id FROM automation_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise AutomationError(404, "Automation plan not found")
        if row["workflow_id"]:
            raise AutomationError(409, "This plan is already saved; propose a new version for future executions")
        status = "needs_clarification" if proposal.clarifying_questions else "awaiting_approval"
        db.execute("UPDATE automation_plans SET title=?,summary=?,definition=?,status=?,questions=?,schedule_at=?,schedule_recurrence=?,schedule_timezone=?,approval_hash=NULL,approved_at=NULL,updated_at=? WHERE id=?", (proposal.title, proposal.summary, json.dumps(definition), status, json.dumps(proposal.clarifying_questions), *schedule, now(), plan_id))
        audit(db, "automation.plan_revised", "automation_plan", plan_id, {"status": status})
        return _render(db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone())


def approve_plan(plan_id: str, expected_hash: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise AutomationError(404, "Automation plan not found")
        if row["status"] not in {"awaiting_approval", "approved"}:
            raise AutomationError(409, "This plan needs clarification before approval")
        definition = json.loads(row["definition"])
        payload = plan_payload(row["title"], row["summary"], definition, (row["schedule_at"], row["schedule_recurrence"], row["schedule_timezone"]))
        actual_hash = fingerprint(payload)
        if expected_hash != actual_hash:
            raise AutomationError(409, "The plan changed; review its current steps before approving")
        if row["status"] != "approved":
            approval_id, timestamp = str(uuid.uuid4()), now()
            db.execute("INSERT INTO automation_approvals(id,plan_id,definition_hash,approved_at) VALUES(?,?,?,?)", (approval_id, plan_id, actual_hash, timestamp))
            db.execute("UPDATE automation_plans SET status='approved',approval_hash=?,approved_at=?,updated_at=? WHERE id=?", (actual_hash, timestamp, timestamp, plan_id))
            audit(db, "automation.plan_approved", "automation_plan", plan_id, {"approval_id": approval_id})
        return _render(db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone())


def cancel_plan(plan_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT status,workflow_id FROM automation_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise AutomationError(404, "Automation plan not found")
        if row["workflow_id"]:
            raise AutomationError(409, "This plan is saved; pause or disable its workflow schedule instead")
        if row["status"] not in {"needs_clarification", "awaiting_approval"}:
            raise AutomationError(409, "This plan can no longer be cancelled")
        db.execute("UPDATE automation_plans SET status='cancelled',updated_at=? WHERE id=?", (now(), plan_id))
        audit(db, "automation.plan_cancelled", "automation_plan", plan_id)
        return _render(db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone())


def save_plan(plan_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM automation_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise AutomationError(404, "Automation plan not found")
        if row["status"] != "approved" or not row["approval_hash"]:
            raise AutomationError(409, "Approve the current plan before saving or running it")
        if row["workflow_id"]:
            workflow = db.execute("SELECT id,name,enabled FROM workflows WHERE id=?", (row["workflow_id"],)).fetchone()
            if workflow:
                return {"workflow_id": workflow["id"], "name": workflow["name"], "saved": True}
        definition = json.loads(row["definition"])
        schedule = (row["schedule_at"], row["schedule_recurrence"], row["schedule_timezone"])
        payload = plan_payload(row["title"], row["summary"], definition, schedule)
        if fingerprint(payload) != row["approval_hash"]:
            raise AutomationError(409, "The approved plan no longer matches its content")
        approval = db.execute("SELECT id FROM automation_approvals WHERE plan_id=? AND definition_hash=? ORDER BY approved_at DESC LIMIT 1", (plan_id, row["approval_hash"])).fetchone()
        if not approval:
            raise AutomationError(409, "The plan approval is unavailable")
        definition["automation"] = {"plan_id": plan_id, "approval_id": approval["id"], "summary": row["summary"], "schedule_at": schedule[0], "schedule_recurrence": schedule[1], "schedule_timezone": schedule[2]}
        workflow_id, timestamp = str(uuid.uuid4()), now()
        db.execute("INSERT INTO workflows(id,name,description,definition,enabled,created_at,updated_at) VALUES(?,?,?,?,1,?,?)", (workflow_id, row["title"], row["summary"], json.dumps(definition), timestamp, timestamp))
        if schedule[0]:
            db.execute("INSERT INTO workflow_schedules(workflow_id,enabled,next_run_at,recurrence,timezone,updated_at) VALUES(?,1,?,?,?,?)", (workflow_id, *schedule, timestamp))
        db.execute("UPDATE automation_plans SET workflow_id=?,updated_at=? WHERE id=?", (workflow_id, timestamp, plan_id))
        audit(db, "automation.plan_saved", "workflow", workflow_id, {"plan_id": plan_id})
        return {"workflow_id": workflow_id, "name": row["title"], "saved": True}


def verify_run_approval(run_id: str) -> None:
    with connect() as db:
        run = db.execute("SELECT workflow_name,definition FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ToolError("state_conflict", "The run is unavailable")
        definition = json.loads(run["definition"])
        if not any(node.get("type") == "tool" for node in definition.get("nodes", [])):
            return
        metadata = definition.get("automation") or {}
        approval = db.execute("SELECT plan_id,definition_hash FROM automation_approvals WHERE id=?", (metadata.get("approval_id"),)).fetchone()
    if not approval or approval["plan_id"] != metadata.get("plan_id"):
        raise ToolError("permission_denied", "An approved automation is required for tool execution")
    payload = plan_payload(run["workflow_name"], metadata.get("summary"), definition, (metadata.get("schedule_at"), metadata.get("schedule_recurrence"), metadata.get("schedule_timezone")))
    if fingerprint(payload) != approval["definition_hash"]:
        raise ToolError("permission_denied", "The tool action differs from its approved plan")
    ids = {node["id"] for node in definition["nodes"]}
    for node in definition["nodes"]:
        if node.get("type") == "tool":
            parents = {edge["source"] for edge in definition["edges"] if edge["target"] == node["id"]}
            validate_tool_node(node, ids, parents)
