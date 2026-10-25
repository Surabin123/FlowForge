"""DAG validation and durable, bounded workflow execution."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections import deque

from .config import settings
from .database import connect, now
from .observability import metrics, request_id_context, outbound_request
from .tool_registry import ToolError

logger = logging.getLogger("flowforge.engine")


class WorkflowError(ValueError):
    """Invalid workflow definition detected before execution."""


class WorkflowFailure(Exception):
    def __init__(self, category: str, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.retryable = retryable


def validate_output_schemas(nodes):
    from jsonschema import Draft202012Validator, SchemaError
    for node in nodes:
        schema = node.get("output_schema")
        if schema:
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise WorkflowError(f"Invalid output schema for node {node.get('id')}: {exc.message}") from exc


def validate_dag(nodes, edges):
    ids = [str(node.get("id", "")) for node in nodes]
    if not all(ids) or len(set(ids)) != len(ids):
        raise WorkflowError("Every node needs a unique id")
    known = set(ids)
    incoming = {node_id: 0 for node_id in ids}
    children = {node_id: [] for node_id in ids}
    for edge in edges:
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        if source not in known or target not in known:
            raise WorkflowError("Every edge must refer to an existing node")
        if source == target:
            raise WorkflowError("A node cannot depend on itself")
        if target not in children[source]:
            children[source].append(target)
            incoming[target] += 1
    ready = deque(node_id for node_id, count in incoming.items() if count == 0)
    order = []
    while ready:
        node_id = ready.popleft()
        order.append(node_id)
        for child_id in children[node_id]:
            incoming[child_id] -= 1
            if incoming[child_id] == 0:
                ready.append(child_id)
    if len(order) != len(ids):
        raise WorkflowError("Workflow dependencies contain a cycle")
    return order


async def _perform(node, inputs):
    kind = node.get("type", "transform")
    config = node.get("config") or {}
    if kind == "manual":
        return {"approved": True, "message": config.get("message", "Approved by the operator")}, None
    if kind == "transform":
        return config.get("output", inputs), None
    if kind == "http":
        raise WorkflowFailure("configuration", "HTTP nodes are disabled until an allowlist can be configured")
    if kind != "agent":
        raise WorkflowFailure("configuration", "Unsupported workflow node type")

    if not settings.ai_api_key:
        raise WorkflowFailure("configuration", "AI provider is not configured")
    import httpx

    model = settings.ai_model
    instructions = str(config.get("prompt", "Process the provided input."))
    input_schema = {}
    output_schema = node.get("output_schema") or {}
    if config.get("agent_id"):
        with connect() as db:
            agent = db.execute("SELECT * FROM agents WHERE id=?", (config["agent_id"],)).fetchone()
        if not agent:
            raise WorkflowFailure("configuration", "Selected agent no longer exists")
        instructions = agent["instructions"] or agent["purpose"] or instructions
        model = agent["model"] or model
        try:
            input_schema = json.loads(agent["input_schema"])
            output_schema = output_schema or json.loads(agent["output_schema"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise WorkflowFailure("configuration", "Stored agent schema is invalid") from exc
    if not model:
        raise WorkflowFailure("configuration", "AI model is not configured")
    if input_schema:
        from jsonschema import ValidationError, validate
        try:
            validate(inputs, input_schema)
        except ValidationError as exc:
            raise WorkflowFailure("input_validation", "Node input does not match the selected agent schema") from exc

    try:
        prompt = (
            instructions
            + "\nReturn only JSON when a schema is provided. Output schema: "
            + json.dumps(output_schema, ensure_ascii=False)
            + "\nInput: "
            + json.dumps(inputs, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowFailure("invalid_input", "Workflow input cannot be encoded as JSON") from exc
    if len(prompt) > settings.max_ai_input_chars:
        raise WorkflowFailure("input_too_large", "AI node input exceeds the configured character limit")

    try:
        response = await outbound_request("POST", settings.ai_base_url.rstrip("/") + "/chat/completions", integration="ai", operation="workflow_completion", timeout=httpx.Timeout(45, connect=10), headers={"Authorization": "Bearer " + settings.ai_api_key}, json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2})
    except httpx.TimeoutException as exc:
        raise WorkflowFailure("provider_timeout", "AI provider request timed out", retryable=True) from exc
    except httpx.TransportError as exc:
        raise WorkflowFailure("provider_network", "AI provider connection failed", retryable=True) from exc
    if response.status_code >= 400:
        retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
        category = "provider_rate_limit" if response.status_code == 429 else "provider_http"
        raise WorkflowFailure(category, f"AI provider returned HTTP {response.status_code}", retryable=retryable)
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise WorkflowFailure("provider_protocol", "AI provider returned an invalid response", retryable=True) from exc
    if not isinstance(content, str) or not content.strip():
        raise WorkflowFailure("provider_protocol", "AI provider returned empty content", retryable=True)
    usage = payload.get("usage") if isinstance(payload, dict) else None
    tokens_total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(tokens_total, int) and tokens_total >= 0:
        metrics.increment("ai.tokens.reported", amount=tokens_total)
    if output_schema:
        try:
            output = json.loads(content)
        except json.JSONDecodeError as exc:
            raise WorkflowFailure("invalid_output", "AI response was not valid JSON for the configured output schema") from exc
        from jsonschema import ValidationError, validate
        try:
            validate(output, output_schema)
        except ValidationError as exc:
            raise WorkflowFailure("output_validation", "AI response does not match the selected agent output schema") from exc
        return output, usage
    return {"text": content, "usage": usage if isinstance(usage, dict) else {}}, usage


def _attempt_limit(node: dict) -> int:
    configured = node.get("max_attempts", settings.workflow_max_attempts)
    try:
        return max(1, min(5, int(configured)))
    except (TypeError, ValueError):
        raise WorkflowFailure("configuration", "Node max_attempts must be an integer")


def _timeout_seconds(node: dict) -> int:
    try:
        return max(1, min(120, int(node.get("timeout_seconds", 30))))
    except (TypeError, ValueError):
        raise WorkflowFailure("configuration", "Node timeout_seconds must be an integer")


def _classify(exc: Exception) -> WorkflowFailure:
    if isinstance(exc, WorkflowFailure):
        return exc
    if isinstance(exc, ToolError):
        return WorkflowFailure(exc.category, exc.reason, retryable=exc.retryable)
    if isinstance(exc, asyncio.TimeoutError):
        return WorkflowFailure("timeout", "Node exceeded its configured timeout", retryable=True)
    try:
        from jsonschema import ValidationError
        if isinstance(exc, ValidationError):
            return WorkflowFailure("output_validation", "Node output does not match its configured schema")
    except ImportError:
        pass
    if isinstance(exc, (TypeError, ValueError, OverflowError)):
        return WorkflowFailure("invalid_output", "Node output could not be serialized as JSON")
    return WorkflowFailure("internal_error", "Node failed due to an internal error")


def _store_attempt(run_id: str, node_id: str, attempt: dict, *, status: str, error: WorkflowFailure | None = None, output: str | None = None, duration_ms: int | None = None) -> None:
    with connect() as db:
        row = db.execute("SELECT attempt_details FROM node_runs WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()
        details = json.loads(row["attempt_details"] or "[]") if row else []
        details.append(attempt)
        db.execute(
            "UPDATE node_runs SET status=?,finished_at=?,attempt_started_at=NULL,output=?,error=?,error_category=?,failure_reason=?,duration_ms=COALESCE(duration_ms,0)+?,attempt_details=?,logs=? WHERE run_id=? AND node_id=?",
            (status, None if status == "paused" else now(), output, error.reason if error else None, error.category if error else None, error.reason if error else None, duration_ms or 0, json.dumps(details, separators=(",", ":")), json.dumps([attempt.get("reason")] if error else []), run_id, node_id),
        )


async def _execute_node(run_id: str, node_id: str, node: dict, inputs: dict):
    if node.get("type") == "manual":
        with connect() as db:
            row = db.execute("SELECT status,attempts FROM node_runs WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()
            if not row or row["status"] not in {"queued", "skipped"}:
                raise WorkflowFailure("state_conflict", "Node is not in a startable state")
            started = now()
            db.execute("UPDATE node_runs SET status='paused',started_at=COALESCE(started_at,?),attempt_started_at=NULL,finished_at=NULL,attempts=attempts+1,error=NULL,error_category=NULL,failure_reason=NULL WHERE run_id=? AND node_id=?", (started, run_id, node_id))
            attempt_number = row["attempts"] + 1
        _store_attempt(run_id, node_id, {"attempt": attempt_number, "status": "paused", "started_at": started, "finished_at": None, "duration_ms": 0}, status="paused")
        return node_id, None, "paused"

    try:
        max_attempts = _attempt_limit(node)
        timeout_seconds = _timeout_seconds(node)
    except WorkflowFailure as exc:
        return await _persist_preflight_failure(run_id, node_id, exc)

    for attempt_number in range(1, max_attempts + 1):
        started_at = now()
        with connect() as db:
            claimed = db.execute(
                "UPDATE node_runs SET status='running',started_at=COALESCE(started_at,?),attempt_started_at=?,finished_at=NULL,attempts=attempts+1,error=NULL,error_category=NULL,failure_reason=NULL WHERE run_id=? AND node_id=? AND status IN ('queued','failed','skipped')",
                (started_at, started_at, run_id, node_id),
            )
            if not claimed.rowcount:
                row = db.execute("SELECT attempts,status FROM node_runs WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()
                if row and row["status"] == "completed":
                    return node_id, None, "completed"
                raise WorkflowFailure("state_conflict", "Node is already active or no longer startable")
            actual_attempt = db.execute("SELECT attempts FROM node_runs WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()["attempts"]
        started = time.perf_counter()
        metrics.increment("workflow.node.attempts", node.get("type", "transform"))
        logger.info("workflow.node.attempt.started", extra={"flowforge": {"run_id": run_id, "node_id": node_id, "attempt": actual_attempt}})
        try:
            if node.get("type") == "tool":
                from .tool_registry import execute_tool
                result = await asyncio.wait_for(execute_tool(node, inputs, run_id, node_id), timeout=timeout_seconds)
                usage = None
            else:
                result, usage = await asyncio.wait_for(_perform(node, inputs), timeout=timeout_seconds)
            if node.get("output_schema"):
                from jsonschema import ValidationError, validate
                try:
                    validate(result, node["output_schema"])
                except ValidationError as exc:
                    raise WorkflowFailure("output_validation", "Node output does not match its configured schema") from exc
            try:
                serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError, OverflowError) as exc:
                raise WorkflowFailure("invalid_output", "Node output could not be serialized as JSON") from exc
            output_bytes = len(serialized.encode("utf-8"))
            if output_bytes > settings.max_workflow_output_bytes:
                raise WorkflowFailure("output_too_large", "Node output exceeds the configured size limit")
            duration_ms = round((time.perf_counter() - started) * 1000)
            detail = {"attempt": actual_attempt, "status": "completed", "started_at": started_at, "finished_at": now(), "duration_ms": duration_ms, "output_bytes": output_bytes}
            if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
                detail["tokens_total"] = usage["total_tokens"]
            _store_attempt(run_id, node_id, detail, status="completed", output=serialized, duration_ms=duration_ms)
            metrics.increment("workflow.node.completed", node.get("type", "transform"))
            metrics.observe("workflow.node.duration_ms", duration_ms, node.get("type", "transform"), "completed")
            logger.info("workflow.node.attempt.completed", extra={"flowforge": {"run_id": run_id, "node_id": node_id, "attempt": actual_attempt, "duration_ms": duration_ms, "output_bytes": output_bytes}})
            return node_id, result, "completed"
        except asyncio.CancelledError:
            duration_ms = round((time.perf_counter() - started) * 1000)
            failure = WorkflowFailure("cancelled", "Node execution was cancelled")
            detail = {"attempt": actual_attempt, "status": "cancelled", "started_at": started_at, "finished_at": now(), "duration_ms": duration_ms, "category": failure.category, "reason": failure.reason}
            _store_attempt(run_id, node_id, detail, status="cancelled", error=failure, duration_ms=duration_ms)
            metrics.increment("workflow.node.cancelled", node.get("type", "transform"))
            logger.info("workflow.node.attempt.cancelled", extra={"flowforge": {"run_id": run_id, "node_id": node_id, "attempt": actual_attempt, "category": failure.category}})
            raise
        except Exception as exc:
            failure = _classify(exc)
            duration_ms = round((time.perf_counter() - started) * 1000)
            retry = failure.retryable and actual_attempt < max_attempts
            detail = {"attempt": actual_attempt, "status": "retrying" if retry else "failed", "started_at": started_at, "finished_at": now(), "duration_ms": duration_ms, "category": failure.category, "reason": failure.reason, "retryable": failure.retryable}
            if retry:
                with connect() as db:
                    row = db.execute("SELECT attempt_details FROM node_runs WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()
                    details = json.loads(row["attempt_details"] or "[]") if row else []
                    details.append(detail)
                    db.execute("UPDATE node_runs SET status='queued',error_category=?,failure_reason=?,duration_ms=COALESCE(duration_ms,0)+?,attempt_details=?,logs=? WHERE run_id=? AND node_id=?", (failure.category, failure.reason, duration_ms, json.dumps(details, separators=(",", ":")), json.dumps([failure.reason]), run_id, node_id))
                metrics.increment("workflow.node.retries", failure.category)
                metrics.observe("workflow.node.duration_ms", duration_ms, node.get("type", "transform"), "retry")
                logger.warning("workflow.node.attempt.retry", extra={"flowforge": {"run_id": run_id, "node_id": node_id, "attempt": actual_attempt, "category": failure.category, "retryable": True, "duration_ms": duration_ms}})
                ceiling = min(settings.workflow_retry_cap_seconds, settings.workflow_retry_base_seconds * (2 ** (actual_attempt - 1)))
                await asyncio.sleep(random.uniform(0, max(0, ceiling)))
                continue
            _store_attempt(run_id, node_id, detail, status="failed", error=failure, duration_ms=duration_ms)
            metrics.increment("workflow.node.failed", failure.category)
            metrics.observe("workflow.node.duration_ms", duration_ms, node.get("type", "transform"), "failed")
            logger.error("workflow.node.attempt.failed", extra={"flowforge": {"run_id": run_id, "node_id": node_id, "attempt": actual_attempt, "category": failure.category, "retryable": failure.retryable, "duration_ms": duration_ms}})
            return node_id, None, "failed"
    return node_id, None, "failed"


async def _persist_preflight_failure(run_id: str, node_id: str, failure: WorkflowFailure):
    timestamp = now()
    attempt = {"attempt": 0, "status": "failed", "started_at": timestamp, "finished_at": timestamp, "duration_ms": 0, "category": failure.category, "reason": failure.reason}
    _store_attempt(run_id, node_id, attempt, status="failed", error=failure, duration_ms=0)
    metrics.increment("workflow.node.failed", failure.category)
    return node_id, None, "failed"


def _finish_run(run_id: str, status: str, segment_ms: int, *, category: str | None = None, reason: str | None = None, log: list[str] | None = None):
    with connect() as db:
        changed = db.execute(
            "UPDATE runs SET status=?,finished_at=?,duration_ms=COALESCE(duration_ms,0)+?,error_category=?,failure_reason=?,error=?,logs=? WHERE id=? AND status='running'",
            (status, None if status == "paused" else now(), segment_ms, category, reason, reason, json.dumps(log or [], ensure_ascii=False), run_id),
        )
        return bool(changed.rowcount)


async def execute_run(run_id, workflow_id, workflow_name, definition):
    """Execute a run only after acquiring its persisted execution claim."""
    executor_id = str(uuid.uuid4())
    with connect() as db:
        claimed = db.execute("UPDATE runs SET execution_id=? WHERE id=? AND status='running' AND execution_id IS NULL", (executor_id, run_id))
        if not claimed.rowcount:
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            return {"id": run_id, "status": row["status"] if row else "missing", "logs": []}
        row = db.execute("SELECT request_id FROM runs WHERE id=?", (run_id,)).fetchone()
    request_id = row["request_id"] if row else None
    context_token = request_id_context.set(request_id) if request_id else None
    segment_started = time.perf_counter()
    log = []
    try:
        nodes = definition["nodes"]
        edges = definition.get("edges", [])
        try:
            order = validate_dag(nodes, edges)
            validate_output_schemas(nodes)
            if any(node.get("type") == "tool" for node in nodes):
                from .automation import verify_run_approval
                verify_run_approval(run_id)
        except (WorkflowError, ToolError) as exc:
            category = exc.category if isinstance(exc, ToolError) else "workflow_invalid"
            _finish_run(run_id, "failed", 0, category=category, reason=str(exc), log=["Workflow definition or permission validation failed"])
            metrics.increment("workflow.runs.completed", "failed")
            return {"id": run_id, "status": "failed", "logs": ["Workflow definition or permission validation failed"]}
        node_map = {str(node["id"]): node for node in nodes}
        parents = {str(node["id"]): [] for node in nodes}
        for edge in edges:
            parents[str(edge["target"])].append(str(edge["source"]))
        with connect() as db:
            for node in nodes:
                db.execute("INSERT OR IGNORE INTO node_runs(id,run_id,node_id,node_name,status) VALUES(?,?,?,?,?)", (str(uuid.uuid4()), run_id, str(node["id"]), str(node.get("name", node["id"])), "queued"))
            prior = db.execute("SELECT node_id,status,output FROM node_runs WHERE run_id=?", (run_id,)).fetchall()
            states = {row["node_id"]: row["status"] for row in prior}
            outputs = {}
            for item in prior:
                if item["status"] == "completed" and item["output"] is not None:
                    outputs[item["node_id"]] = json.loads(item["output"])
        pending = {node_id for node_id in order if states.get(node_id) != "completed"}
        run_failed = False
        run_paused = False
        while pending:
            remaining = settings.max_workflow_duration_seconds - (time.perf_counter() - segment_started)
            if remaining <= 0:
                run_failed = True
                log.append("Workflow exceeded its execution time limit")
                break
            ready = [node_id for node_id in order if node_id in pending and all(parent in outputs for parent in parents[node_id])]
            if not ready:
                run_failed = True
                with connect() as db:
                    for node_id in pending:
                        db.execute("UPDATE node_runs SET status='skipped',finished_at=?,error_category='dependency_failed',failure_reason='Prerequisite output is unavailable' WHERE run_id=? AND node_id=? AND status IN ('queued','skipped')", (now(), run_id, node_id))
                log.append("No runnable nodes remain because prerequisite outputs are unavailable")
                break
            batch = ready[:max(1, settings.max_parallel_nodes)]
            try:
                results = await asyncio.wait_for(asyncio.gather(*(_execute_node(run_id, node_id, node_map[node_id], {parent: outputs[parent] for parent in parents[node_id]}) for node_id in batch)), timeout=remaining)
            except asyncio.TimeoutError:
                run_failed = True
                log.append("Workflow exceeded its execution time limit")
                with connect() as db:
                    db.execute("UPDATE node_runs SET status='failed',error_category='run_timeout',failure_reason='Workflow exceeded its execution time limit' WHERE run_id=? AND status='cancelled'", (run_id,))
                break
            pending.difference_update(batch)
            batch_failed = False
            for node_id, output, state in results:
                log.append(f"Node {node_id}: {state}")
                if state == "paused":
                    run_paused = True
                elif state == "failed":
                    run_failed = True
                    batch_failed = True
                elif state == "completed":
                    outputs[node_id] = output
            if batch_failed:
                with connect() as db:
                    for node_id in pending:
                        db.execute("UPDATE node_runs SET status='skipped',finished_at=?,error_category='dependency_failed',failure_reason='Not started because a node failed in this run' WHERE run_id=? AND node_id=? AND status IN ('queued','skipped')", (now(), run_id, node_id))
                pending.clear()
                break
            if run_paused:
                with connect() as db:
                    for node_id in pending:
                        db.execute("UPDATE node_runs SET status='skipped',error_category='awaiting_approval',failure_reason='Will resume after manual approval' WHERE run_id=? AND node_id=? AND status='queued'", (run_id, node_id))
                break
        status = "failed" if run_failed else "paused" if run_paused else "completed"
        if run_failed and pending:
            with connect() as db:
                db.execute("UPDATE node_runs SET status='skipped',finished_at=?,error_category='dependency_failed',failure_reason='Run ended before this node started' WHERE run_id=? AND status IN ('queued','skipped')", (now(), run_id))
        reason = "One or more workflow nodes failed" if run_failed else None
        category = "node_failed" if run_failed else None
        if run_failed and "Workflow exceeded its execution time limit" in log:
            reason, category = "Workflow exceeded its execution time limit", "run_timeout"
        finished = _finish_run(run_id, status, round((time.perf_counter() - segment_started) * 1000), category=category, reason=reason, log=log)
        if not finished:
            with connect() as db:
                current = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            return {"id": run_id, "status": current["status"] if current else "missing", "logs": log}
        metrics.increment("workflow.runs.completed", status)
        metrics.observe("workflow.run.duration_ms", (time.perf_counter() - segment_started) * 1000, status)
        logger.info("workflow.run.finished", extra={"flowforge": {"run_id": run_id, "workflow_id": workflow_id, "status": status, "duration_ms": round((time.perf_counter() - segment_started) * 1000)}})
        return {"id": run_id, "status": status, "logs": log}
    except asyncio.CancelledError:
        duration_ms = round((time.perf_counter() - segment_started) * 1000)
        with connect() as db:
            current = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if current and current["status"] == "running":
                db.execute("UPDATE node_runs SET status='failed',finished_at=?,error_category='interrupted',failure_reason='Execution interrupted by process shutdown; reconcile external effects before retrying' WHERE run_id=? AND (status='running' OR (status='cancelled' AND error_category='cancelled'))", (now(), run_id))
                db.execute("UPDATE node_runs SET status='skipped',finished_at=?,error_category='interrupted',failure_reason='Not started before process shutdown' WHERE run_id=? AND status='queued'", (now(), run_id))
                db.execute("UPDATE runs SET status='failed',finished_at=?,duration_ms=COALESCE(duration_ms,0)+?,error_category='interrupted',failure_reason='Execution interrupted by process shutdown; reconcile external effects before retrying',error='Execution interrupted; inspect external effects before retrying' WHERE id=? AND status='running'", (now(), duration_ms, run_id))
                metrics.increment("workflow.runs.completed", "interrupted")
        raise
    except Exception as exc:
        duration_ms = round((time.perf_counter() - segment_started) * 1000)
        logger.error("workflow.run.internal_failure", extra={"flowforge": {"run_id": run_id, "workflow_id": workflow_id, "category": "internal_error", "exception_type": type(exc).__name__}})
        with connect() as db:
            db.execute("UPDATE node_runs SET status='failed',finished_at=?,error_category='internal_error',failure_reason='Workflow executor failed unexpectedly' WHERE run_id=? AND status='running'", (now(), run_id))
        _finish_run(run_id, "failed", duration_ms, category="internal_error", reason="Workflow executor failed unexpectedly", log=log + ["Workflow executor failed unexpectedly"])
        metrics.increment("workflow.runs.completed", "failed")
        return {"id": run_id, "status": "failed", "logs": log}
    finally:
        if context_token is not None:
            request_id_context.reset(context_token)
