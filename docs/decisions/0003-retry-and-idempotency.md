# ADR 0003: Bounded retries with at-least-once external calls

Status: accepted (2026-10-20)

## Decision

Retry only classified transient provider/network failures and timeouts, using a small bounded attempt count with exponential backoff and jitter. Treat schema/configuration/validation errors and most HTTP 4xx responses as permanent. A user retry creates a new run; it does not silently resume successful nodes from an earlier run.

Manual run creation accepts an optional `Idempotency-Key`, scoped to the workflow and stored under a partial unique SQLite index. Repeated starts with that key return the original run. Workflow execution claims and node start transitions are conditional database updates, preventing a second executor from starting the same persisted run/node in the supported single-process deployment. In-process admission limits bound concurrent runs; this does not provide cross-process coordination.

## Semantics and trade-offs

An external operation can succeed immediately before the process crashes, leaving the local attempt marked running. Recovery cannot know whether the remote side effect occurred. A retry may therefore repeat it: execution is at-least-once across that crash window, never exactly-once. AI requests may be billed more than once. Future side-effecting integrations must use a provider idempotency key, query/reconcile remote state, or require explicit user review before retry.

Transform nodes are local and deterministic for a fixed saved definition. AI calls are not assumed idempotent. Manual approval is persisted before continuation.
