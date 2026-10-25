# ADR 0002: SQLite and one scheduler process

Status: accepted (2026-10-20)

## Decision

Retain SQLite and the five-second scheduler for the single-user deployment. Persist schedule occurrences with unique keys and keep the database plus uploaded originals on the configured persistent disk in Render.

## Trade-offs

The design is small and recoverable across ordinary restarts, but SQLite serializes writers and the scheduler has no cross-process lease. Run process count must remain one. Restart marks in-flight work failed and does not resume a node. Persistent disk is not a backup; periodic online backups must be copied off the disk and restore drills remain operator work.

## Revisit when

Use PostgreSQL when query-plan and write-contention benchmarks show SQLite is the limiting factor or multiple app instances are required. Use a separate worker/queue when measured execution time or restart recovery requirements need jobs independent of the web process. Those changes require a lease/claim protocol and migration plan.
