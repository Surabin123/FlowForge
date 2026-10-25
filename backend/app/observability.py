"""Small bounded process-local metrics and safe structured logs."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import asyncio
from collections import defaultdict, deque
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_context: ContextVar[str | None] = ContextVar("flowforge_request_id", default=None)
outbound_client_context: ContextVar[object | None] = ContextVar("flowforge_outbound_client", default=None)


class BoundedMetrics:
    """Fixed-cardinality counters and bounded duration samples; never authoritative state."""

    def __init__(self, max_series: int = 64, sample_limit: int = 512):
        self._max_series = max_series
        self._sample_limit = sample_limit
        self._counters: dict[tuple[str, tuple[str, ...]], int] = defaultdict(int)
        self._samples: dict[tuple[str, tuple[str, ...]], deque[float]] = {}
        self._lock = threading.Lock()

    def increment(self, name: str, *labels: str, amount: int = 1) -> None:
        key = (name, tuple(labels))
        with self._lock:
            if key not in self._counters and len(self._counters) >= self._max_series - 1:
                key = ("metrics.series_overflow", ())
            self._counters[key] += amount

    def observe(self, name: str, value: float, *labels: str) -> None:
        key = (name, tuple(labels))
        with self._lock:
            if key not in self._samples:
                if len(self._samples) >= self._max_series - 1:
                    key = ("metrics.sample_series_overflow", ())
                self._samples.setdefault(key, deque(maxlen=self._sample_limit))
            self._samples[key].append(float(value))

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            samples = {key: tuple(values) for key, values in self._samples.items()}
        return {
            "counters": {_series_name(key): value for key, value in sorted(counters.items())},
            "durations": {
                _series_name(key): _summary(values)
                for key, values in sorted(samples.items())
            },
            "process": process_usage(),
        }


def _series_name(key: tuple[str, tuple[str, ...]]) -> str:
    name, labels = key
    return name if not labels else name + "{" + ",".join(labels) + "}"


def _summary(values: tuple[float, ...]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50": None, "p95": None, "max": None}
    def percentile(q: float) -> float:
        return ordered[max(0, min(len(ordered) - 1, int((len(ordered) - 1) * q + .5)))]
    return {"samples": len(ordered), "p50": percentile(.50), "p95": percentile(.95), "max": ordered[-1]}


def process_usage() -> dict:
    result = {"cpu_seconds": sum(os.times()[:2]), "peak_rss_bytes": None}
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_rss_bytes"] = int(rss if sys.platform == "darwin" else rss * 1024)
    except (ImportError, AttributeError, OSError):
        pass
    return result


class SafeJsonFormatter(logging.Formatter):
    _safe_fields = {
        "request_id", "run_id", "workflow_id", "node_id", "attempt", "status",
        "category", "duration_ms", "retryable", "http_status", "integration",
        "operation", "reason", "route", "method", "tokens_total", "output_bytes", "exception_type",
    }

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "flowforge", {})
        if not isinstance(fields, dict):
            fields = {}
        safe = {key: value for key, value in fields.items() if key in self._safe_fields and isinstance(value, (str, int, float, bool, type(None)))}
        request_id = request_id_context.get()
        if request_id:
            safe["request_id"] = request_id
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **safe,
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


metrics = BoundedMetrics()


async def observed_request(client, method: str, url: str, *, integration: str, operation: str, **kwargs):
    """Issue one outbound request and record latency/status without recording URL or payload."""
    started = time.perf_counter()
    metrics.increment("http.outbound.attempts", integration, operation)
    try:
        response = await client.request(method, url, **kwargs)
        status_group = f"{response.status_code // 100}xx"
        if response.status_code >= 400:
            metrics.increment("http.outbound.failures", integration, status_group)
        return response
    except asyncio.CancelledError:
        metrics.increment("http.outbound.failures", integration, "cancelled")
        raise
    except Exception:
        metrics.increment("http.outbound.failures", integration, "exception")
        raise
    finally:
        metrics.observe("http.outbound.duration_ms", (time.perf_counter() - started) * 1000, integration, operation)


async def outbound_request(method: str, url: str, *, integration: str, operation: str, timeout=None, **kwargs):
    """Reuse the application pool when available, otherwise own a short-lived client."""
    client = outbound_client_context.get()
    if client is not None:
        if timeout is not None:
            kwargs["timeout"] = timeout
        return await observed_request(client, method, url, integration=integration, operation=operation, **kwargs)
    import httpx
    async with httpx.AsyncClient(timeout=timeout or httpx.Timeout(30, connect=10)) as owned_client:
        return await observed_request(owned_client, method, url, integration=integration, operation=operation, **kwargs)
