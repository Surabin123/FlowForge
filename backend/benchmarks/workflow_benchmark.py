"""Reproducible local workflow and scheduler benchmark; uses only synthetic data."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path

from starlette.datastructures import UploadFile

from app import config, engine
from app.database import connect, init_db, now
from app.engine import WorkflowFailure, execute_run
from app.main import upload_document
from app.observability import metrics, process_usage
from app.scheduler import process_due


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * q + .5)))
    return ordered[index]


def graph(size: int) -> tuple[list[dict], list[dict]]:
    nodes = [{"id": f"n{i}", "type": "transform", "config": {"output": {"index": i}}} for i in range(size)]
    edges = []
    for i in range(1, size):
        edges.append({"source": f"n{i-1}", "target": f"n{i}"})
        if i > 1 and i % 3 == 0:
            edges.append({"source": f"n{i-2}", "target": f"n{i}"})
    return nodes, edges


def create_run(definition: dict) -> str:
    run_id = str(uuid.uuid4())
    timestamp = now()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,workflow_name,status,started_at,created_at,definition) VALUES(?,?,?,?,?,?)",
            (run_id, "Synthetic benchmark", "running", timestamp, timestamp, json.dumps(definition)),
        )
    return run_id


async def run_once(definition: dict) -> tuple[float, str, list[int]]:
    run_id = create_run(definition)
    started = time.perf_counter()
    result = await execute_run(run_id, None, "Synthetic benchmark", definition)
    elapsed = (time.perf_counter() - started) * 1000
    with connect() as db:
        durations = [row[0] for row in db.execute("SELECT duration_ms FROM node_runs WHERE run_id=? AND duration_ms IS NOT NULL", (run_id,))]
    return elapsed, result["status"], durations


async def measure(name: str, definition: dict, *, iterations: int, warmup: int, concurrency: int = 1, before_each=None) -> dict:
    for _ in range(warmup):
        if before_each:
            await before_each()
        await run_once(definition)
    values: list[float] = []
    node_values: list[int] = []
    failures = 0
    started_all = time.perf_counter()
    complete = 0
    for start in range(0, iterations, concurrency):
        batch = min(concurrency, iterations - start)

        async def execute_item():
            item_started = time.perf_counter()
            if before_each:
                await before_each()
            elapsed, status, node_ms = await run_once(definition)
            return (time.perf_counter() - item_started) * 1000 if before_each else elapsed, status, node_ms

        results = await asyncio.gather(*(execute_item() for _ in range(batch)))
        for elapsed, status, node_ms in results:
            values.append(elapsed)
            node_values.extend(node_ms)
            failures += status != "completed"
            complete += 1
    wall_seconds = time.perf_counter() - started_all
    return {
        "workload": name,
        "samples": len(values),
        "concurrency": concurrency,
        "workflow_latency_ms": {"p50": percentile(values, .50), "p95": percentile(values, .95)},
        "node_latency_ms": {"samples": len(node_values), "p50": percentile(node_values, .50), "p95": percentile(node_values, .95)},
        "throughput_runs_per_second": round(complete / wall_seconds, 3) if wall_seconds else None,
        "failure_rate": failures / complete if complete else None,
        "wall_seconds": round(wall_seconds, 3),
    }


async def main_async(args):
    with tempfile.TemporaryDirectory(prefix="flowforge-benchmark-") as temporary:
        root = Path(temporary)
        config.settings.database = str(root / "benchmark.sqlite3")
        config.settings.documents = str(root / "documents")
        config.settings.max_parallel_nodes = args.node_concurrency
        config.settings.workflow_retry_base_seconds = 0
        config.settings.workflow_max_attempts = 2
        config.settings.max_workflow_output_bytes = 1_000_000
        config.settings.document_path.mkdir(parents=True, exist_ok=True)
        init_db()
        usage_before = process_usage()

        small_nodes, small_edges = graph(5)
        large_nodes, large_edges = graph(40)
        text_payload = ("A synthetic personal study document. " * 1800).encode("utf-8")
        async def upload_fixture():
            record = await upload_document(UploadFile(file=io.BytesIO(text_payload), filename="synthetic-notes.txt", headers=None))
            with connect() as db:
                extracted = db.execute("SELECT extracted_text FROM documents WHERE id=?", (record["id"],)).fetchone()["extracted_text"]
            doc_definition["nodes"][0]["config"]["output"] = {"document_id": record["id"], "text": extracted}

        doc_definition = {
            "nodes": [
                {"id": "document-source", "type": "transform", "config": {"output": {"document_id": "fixture", "text": text_payload.decode("utf-8")}}},
                {"id": "document-handoff", "type": "transform"},
            ],
            "edges": [{"source": "document-source", "target": "document-handoff"}],
        }

        provider_definition = {"nodes": [{"id": "provider", "type": "agent", "config": {"prompt": "synthetic benchmark only"}}], "edges": []}
        original_perform = engine._perform
        calls_by_task: dict[asyncio.Task, int] = {}

        async def simulated_provider(node, inputs):
            task = asyncio.current_task()
            calls_by_task[task] = calls_by_task.get(task, 0) + 1
            await asyncio.sleep(args.provider_delay_ms / 1000)
            if calls_by_task[task] == 1:
                raise WorkflowFailure("provider_network", "synthetic transient provider failure", retryable=True)
            return {"simulated": True}, {"total_tokens": 12}

        engine._perform = simulated_provider
        retry_start = metrics.snapshot()["counters"].get("workflow.node.retries{provider_network}", 0)
        provider_result = await measure("simulated slow/transient provider (no network)", provider_definition, iterations=args.iterations, warmup=args.warmup, concurrency=1)
        retries_after = metrics.snapshot()["counters"].get("workflow.node.retries{provider_network}", 0)
        engine._perform = original_perform

        workloads = [
            await measure("small dependent workflow (5 nodes)", {"nodes": small_nodes, "edges": small_edges}, iterations=args.iterations, warmup=args.warmup),
            await measure("larger dependency graph (40 nodes)", {"nodes": large_nodes, "edges": large_edges}, iterations=args.iterations, warmup=args.warmup),
            await measure("document text extraction plus workflow serialization (synthetic 64KB TXT)", doc_definition, iterations=args.iterations, warmup=args.warmup, before_each=upload_fixture),
            provider_result,
            await measure(f"{args.concurrent_runs} concurrent 10-node executions", {"nodes": graph(10)[0], "edges": graph(10)[1]}, iterations=args.iterations, warmup=args.warmup, concurrency=args.concurrent_runs),
        ]

        with connect() as db:
            tasks_plan = [row["detail"] for row in db.execute("EXPLAIN QUERY PLAN SELECT * FROM tasks WHERE status='open' AND ((due_at IS NOT NULL AND julianday(due_at)<=julianday(?)) OR (reminder_enabled=1 AND reminder_at IS NOT NULL AND julianday(reminder_at)<=julianday(?)))", (now(), now()))]
            schedule_plan = [row["detail"] for row in db.execute("EXPLAIN QUERY PLAN SELECT * FROM workflow_schedules WHERE enabled=1 AND julianday(next_run_at)<=julianday(?)", (now(),))]
        # Keep scheduler writes outside the preceding read transaction. Even an
        # empty-due poll writes scheduler health/state on its own connection.
        scheduler_samples=[]
        for _ in range(args.iterations):
            started=time.perf_counter(); process_due(); scheduler_samples.append((time.perf_counter()-started)*1000)
        with connect() as db:
            db_size = Path(config.settings.database).stat().st_size
        usage = process_usage()
        usage_delta = {"cpu_seconds": usage["cpu_seconds"] - usage_before["cpu_seconds"], "peak_rss_bytes_start": usage_before["peak_rss_bytes"], "peak_rss_bytes_end": usage["peak_rss_bytes"]}
        return {
            "environment": {"os": platform.platform(), "python": sys.version.split()[0], "sqlite": __import__("sqlite3").sqlite_version},
            "method": {"iterations_per_workload": args.iterations, "warmup_per_workload": args.warmup, "provider_simulated_delay_ms": args.provider_delay_ms, "node_concurrency": args.node_concurrency, "concurrent_runs": args.concurrent_runs, "measurement": "perf_counter monotonic wall clock; p95 requires the default 100 measured samples; provider is an in-process stub, not a network benchmark"},
            "workloads": workloads,
            "scheduler_empty_due_poll_ms": {"samples": len(scheduler_samples), "p50": percentile(scheduler_samples, .50), "p95": percentile(scheduler_samples, .95)},
            "provider_retries": retries_after - retry_start,
            "sqlite_query_plans": {"due_tasks": tasks_plan, "due_schedules": schedule_plan},
            "sqlite_file_bytes": db_size,
            "process_usage": usage_delta,
            "limitations": ["Synthetic local data only; no live AI provider, Render disk, or remote network measured.", "CPU and peak RSS are process-level and include interpreter/test harness overhead.", "The document case performs actual local text extraction and then serializes the extracted text through a transform workflow; it is not a PDF benchmark."],
        }


def main():
    # Keep the reproducible result as clean JSON; the engine's structured
    # per-node logs would otherwise dominate the benchmark output and timing.
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--node-concurrency", type=int, default=4)
    parser.add_argument("--concurrent-runs", type=int, default=4)
    parser.add_argument("--provider-delay-ms", type=float, default=20)
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0 or args.concurrent_runs < 1:
        parser.error("iterations and concurrent-runs must be positive; warmup cannot be negative")
    print(json.dumps(asyncio.run(main_async(args)), indent=2))


if __name__ == "__main__":
    main()
