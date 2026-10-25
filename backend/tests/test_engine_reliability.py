import asyncio
import json
import uuid

import pytest

from app import config
from app.database import connect, init_db, now
from app.engine import WorkflowFailure, execute_run
from app.observability import metrics


def make_run(tmp_path, monkeypatch, nodes, edges=None):
    monkeypatch.setattr(config.settings, "database", str(tmp_path / "reliability.db"))
    monkeypatch.setattr(config.settings, "documents", str(tmp_path / "docs"))
    config.settings.document_path.mkdir(parents=True, exist_ok=True)
    init_db()
    run_id = str(uuid.uuid4())
    definition = {"nodes": nodes, "edges": edges or []}
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,workflow_name,status,started_at,created_at,definition) VALUES(?,?,?,?,?,?)",
            (run_id, "Reliability test", "running", now(), now(), json.dumps(definition)),
        )
    return run_id, definition


def node_row(run_id):
    with connect() as db:
        return db.execute("SELECT * FROM node_runs WHERE run_id=? ORDER BY rowid LIMIT 1", (run_id,)).fetchone()


def run_row(run_id):
    with connect() as db:
        return db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def test_transient_node_failure_retries_with_attempt_history(tmp_path, monkeypatch):
    run_id, definition = make_run(tmp_path, monkeypatch, [{"id": "node-a", "type": "transform"}])
    monkeypatch.setattr(config.settings, "workflow_max_attempts", 3)
    monkeypatch.setattr(config.settings, "workflow_retry_base_seconds", 0)
    calls = 0

    async def flaky(node, inputs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise WorkflowFailure("provider_network", "AI provider connection failed", retryable=True)
        return {"ready": True}, None

    monkeypatch.setattr("app.engine._perform", flaky)
    result = asyncio.run(execute_run(run_id, None, "Reliability test", definition))
    row = node_row(run_id)
    attempts = json.loads(row["attempt_details"])
    assert result["status"] == "completed"
    assert row["attempts"] == 3
    assert [item["status"] for item in attempts] == ["retrying", "retrying", "completed"]
    assert row["duration_ms"] is not None and row["error_category"] is None


def test_permanent_failure_is_not_retried_and_dependency_is_skipped(tmp_path, monkeypatch):
    run_id, definition = make_run(
        tmp_path,
        monkeypatch,
        [{"id": "source", "type": "agent"}, {"id": "dependent", "type": "transform"}],
        [{"source": "source", "target": "dependent"}],
    )
    calls = 0

    async def permanent(node, inputs):
        nonlocal calls
        calls += 1
        raise WorkflowFailure("configuration", "AI provider is not configured")

    monkeypatch.setattr("app.engine._perform", permanent)
    result = asyncio.run(execute_run(run_id, None, "Reliability test", definition))
    with connect() as db:
        rows = db.execute("SELECT node_id,status,attempts,error_category,failure_reason FROM node_runs WHERE run_id=? ORDER BY node_id", (run_id,)).fetchall()
    assert result["status"] == "failed"
    assert calls == 1
    source = next(row for row in rows if row["node_id"] == "source")
    dependent = next(row for row in rows if row["node_id"] == "dependent")
    assert source["attempts"] == 1 and source["error_category"] == "configuration"
    assert dependent["status"] == "skipped" and dependent["error_category"] == "dependency_failed"


def test_timeout_is_a_distinct_failure_and_is_bounded(tmp_path, monkeypatch):
    run_id, definition = make_run(tmp_path, monkeypatch, [{"id": "slow", "type": "transform", "timeout_seconds": 1, "max_attempts": 1}])

    async def slow(node, inputs):
        await asyncio.sleep(2)
        return {}, None

    monkeypatch.setattr("app.engine._perform", slow)
    result = asyncio.run(execute_run(run_id, None, "Reliability test", definition))
    row = node_row(run_id)
    assert result["status"] == "failed"
    assert row["attempts"] == 1
    assert row["error_category"] == "timeout"
    assert row["failure_reason"] == "Node exceeded its configured timeout"


def test_provider_rate_limit_retries_and_usage_is_measured_without_logging_secrets(tmp_path, monkeypatch, caplog):
    import httpx
    from app.observability import SafeJsonFormatter
    run_id, definition = make_run(tmp_path, monkeypatch, [{"id": "agent-a", "type": "agent", "config": {"prompt": "private-prompt-marker"}}])
    monkeypatch.setattr(config.settings, "ai_api_key", "private-token-marker")
    monkeypatch.setattr(config.settings, "ai_model", "test-model")
    monkeypatch.setattr(config.settings, "workflow_max_attempts", 2)
    monkeypatch.setattr(config.settings, "workflow_retry_base_seconds", 0)
    original_client = httpx.AsyncClient
    requests = 0

    async def handler(request):
        nonlocal requests
        requests += 1
        assert request.headers["authorization"] == "Bearer private-token-marker"
        if requests == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "result"}}], "usage": {"total_tokens": 11}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: original_client(*args, transport=httpx.MockTransport(handler), **kwargs))
    before = metrics.snapshot()["counters"].get("http.outbound.attempts{ai,workflow_completion}", 0)
    result = asyncio.run(execute_run(run_id, None, "Reliability test", definition))
    after = metrics.snapshot()["counters"].get("http.outbound.attempts{ai,workflow_completion}", 0)
    assert result["status"] == "completed" and requests == 2
    assert after - before == 2
    assert metrics.snapshot()["counters"]["ai.tokens.reported"] >= 11
    rendered = "\n".join(SafeJsonFormatter().format(record) for record in caplog.records)
    assert "private-prompt-marker" not in rendered
    assert "private-token-marker" not in rendered


def test_process_cancellation_is_recorded_as_interruption(tmp_path, monkeypatch):
    run_id, definition = make_run(tmp_path, monkeypatch, [{"id": "waiting", "type": "transform"}])
    entered = asyncio.Event()

    async def blocked(node, inputs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.engine._perform", blocked)

    async def cancel():
        task = asyncio.create_task(execute_run(run_id, None, "Reliability test", definition))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert run_row(run_id)["error_category"] == "interrupted"
    assert node_row(run_id)["status"] == "failed"
    assert node_row(run_id)["error_category"] == "interrupted"


def test_duplicate_executor_cannot_claim_same_run_twice(tmp_path, monkeypatch):
    run_id, definition = make_run(tmp_path, monkeypatch, [{"id": "side-effect", "type": "transform"}])
    entered = asyncio.Event()
    calls = 0

    async def delayed(node, inputs):
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.sleep(0.03)
        return {"done": True}, None

    monkeypatch.setattr("app.engine._perform", delayed)

    async def run_twice():
        first = asyncio.create_task(execute_run(run_id, None, "Reliability test", definition))
        await entered.wait()
        second = await execute_run(run_id, None, "Reliability test", definition)
        final = await first
        return second, final

    duplicate, final = asyncio.run(run_twice())
    assert duplicate["status"] == "running"
    assert final["status"] == "completed"
    assert calls == 1 and node_row(run_id)["attempts"] == 1


def test_metrics_are_bounded_and_report_percentiles():
    from app.observability import BoundedMetrics
    bounded = BoundedMetrics(max_series=2, sample_limit=3)
    for value in range(10):
        bounded.observe("duration", value, "fixed")
    snapshot = bounded.snapshot()
    summary = snapshot["durations"]["duration{fixed}"]
    assert summary == {"samples": 3, "p50": 8.0, "p95": 9.0, "max": 9.0}
    for label in ("other", "third", "fourth"):
        bounded.observe("another-duration", 1, label)
        bounded.increment("counter", label)
    snapshot = bounded.snapshot()
    assert len(snapshot["durations"]) <= 2
    assert len(snapshot["counters"]) <= 2


def test_outbound_request_reuses_context_client_without_creating_one(monkeypatch):
    import httpx
    from app.observability import outbound_client_context, outbound_request
    response = httpx.Response(200, json={"ok": True})

    class Client:
        async def request(self, method, url, **kwargs):
            assert method == "GET" and url == "https://example.test/health"
            return response

    def unexpected_client(*args, **kwargs):
        raise AssertionError("pooled requests must not create another client")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)
    token = outbound_client_context.set(Client())
    try:
        actual = asyncio.run(outbound_request("GET", "https://example.test/health", integration="test", operation="pool_check"))
        assert actual is response
    finally:
        outbound_client_context.reset(token)
