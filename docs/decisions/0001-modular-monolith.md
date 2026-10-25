# ADR 0001: Keep FlowForge a modular monolith

Status: accepted (2026-10-20)

## Decision

Keep the React/Vite frontend and FastAPI backend as one deployable application, with workflow, scheduling, persistence and provider integration in cohesive Python modules. The prepared container serves the built UI from FastAPI. Do not split services or add a broker now.

## Context and trade-offs

FlowForge is a private single-user application with local data and one scheduler. A distributed topology would add network, deployment, queue, tracing and secret-management failure modes without measured throughput or availability requirements. The modular monolith makes a process crash and scheduler ownership easier to reason about. Its limit is that one process and one SQLite file bound concurrency and availability.

## Revisit when

Separate a worker only when measured queue delay or CPU-bound work blocks interactive API service, or when jobs must outlive web-service restarts. Consider PostgreSQL when measured write contention, database size, multi-process access or remote concurrency exceeds the tested SQLite envelope. Consider Redis only if multiple processes need a shared lease/cache or a broker is selected; do not add it for a single process.
