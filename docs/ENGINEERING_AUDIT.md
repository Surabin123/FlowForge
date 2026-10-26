# FlowForge engineering audit

Audit date: 2026-10-20. Scope: repository source and tests at the current working tree. This is a code inspection, not a production or remote deployment audit. Behaviour described as verified below is backed by source paths and/or the local tests named here.

## Architecture and responsibilities

FlowForge is a single-user modular monolith. React/TypeScript/Vite (`frontend/src/App.tsx`) renders task, study, document, workflow and run views. The browser calls FastAPI (`backend/app/main.py`); in the Render container FastAPI serves the built Vite bundle. SQLite (`backend/app/database.py`) stores application records and run state. Uploaded originals live at `FLOWFORGE_DOCUMENTS`. `backend/app/engine.py` validates DAGs and runs nodes. `backend/app/scheduler.py` polls the same database every five seconds and creates persisted notifications and scheduled runs. There is no broker or separate worker.

## Workflow request and state sequence

1. The workflow editor submits `POST /api/workflows`; the API validates the graph and output schemas, then saves its definition and optional schedule in a SQLite transaction.
2. A manual run button submits `POST /api/workflows/{id}/runs`. `start_run` checks the workflow and local concurrency count, inserts a `running` run record, commits, then creates an `asyncio` task in the process-local `live_runs` map.
3. `execute_run` inserts one `queued` `node_runs` row per node, restores outputs for already-completed nodes, and builds a topological order. Ready siblings run concurrently up to `FLOWFORGE_MAX_PARALLEL_NODES`.
4. Before each node call, the node row becomes `running` and `attempts` increments. Transform nodes return configured data/input; manual nodes become `paused`; agent nodes make a provider request; HTTP nodes fail as disabled. Successful results are JSON-serialized and stored with `completed`; failures store `failed` plus a string error. The enclosing run becomes `completed`, `paused`, or `failed`.
5. Manual approval changes paused nodes to completed, the run to running, and schedules another in-process execution task. A failed/cancelled run can be retried through a new run record; it does not resume its prior run.
6. For scheduled runs, `process_due` claims a unique occurrence key and inserts a `queued` run in a transaction. The scheduler starts queued schedule runs and leaves them queued when the local concurrency limit is reached.

Existing state values found: run `queued/running/paused/completed/failed/cancelled`; node `queued/running/paused/completed/failed/skipped/cancelled`. These are string values, not enforced transition constraints. Existing code/tests do not validate every transition.

## Persistence and outbound requests

SQLite connections are created per operation with a 10-second busy timeout and foreign keys enabled. `connect()` commits on normal exit and rolls back on exceptions. Node state is committed before an external call, then result/error is committed after the call. This ordering preserves an attempt record but leaves the known crash window: a remote side effect can succeed and the process can die before recording the result. Replaying can therefore perform that side effect again. There is no exactly-once guarantee or external idempotency key.

Agent nodes and document summaries use a new `httpx.AsyncClient` per operation, with 45-second and 60-second timeouts respectively. They call an OpenAI-compatible `/chat/completions` endpoint. The provider response's token usage is returned for agent outputs but is not persisted as metrics. HTTP workflow nodes are disabled. The GitHub developer view calls `api.github.com`; it uses one request for repositories and three parallel requests for repo activity, with ten-second client timeouts. Provider and GitHub credentials are sent only in Authorization headers. No HTTP connection pool is reused across independent operations.

## Retry, timeout, cancellation, recovery, idempotency

- Node `attempts` increments, but there is no retry loop or transient/permanent error classification. Retry via the UI starts a fresh run and can repeat every prior node.
- Node timeout uses `asyncio.wait_for` (1–120 seconds, default 30), while the AI client's own timeout is 45 seconds. A timeout is caught as a generic failed node; the persisted record does not distinguish it from other failures.
- Cancellation endpoint cancels the local task and writes `cancelled` to active rows. Process interruption leaves `running` rows, which app startup marks `failed`; queued schedule rows are picked up again.
- The scheduler's unique `scheduled_fires.occurrence_key` prevents duplicate claims for an occurrence in one database. It is not a cross-process scheduler lease. `live_runs` is process-local.
- Completed run/node state is authoritative; there is no cache. Transform nodes are deterministic only if their saved workflow definition is unchanged. AI requests can incur repeated cost on retry. Future side-effecting integrations need provider idempotency keys or reconciliation before retries can be considered safe.
- Dependent nodes are not started unless parent outputs exist. A failed ready node causes the run to fail and remaining queued nodes to become skipped. Independent siblings in the same already-running batch may finish after another sibling fails.

## Security and resource limits

Verified controls: Render config requires a generated bearer token; local defaults to loopback and no auth; token comparison is constant-time; uploads are size-limited and restricted to PDF/TXT/Markdown/CSV; document storage names use generated UUIDs; AI summary UI asks before sending text; workflow HTTP nodes are disabled; GitHub is read-only. `.env`, local data and uploads are ignored by Git and Docker build context.

Unresolved risks: one shared bearer token is held in `sessionStorage` and readable by same-origin JavaScript; no login rate limit or token rotation flow exists. The health route is unauthenticated and reports integration configured booleans. Render `FLOWFORGE_ALLOWED_HOSTS=*` weakens host-header filtering. Upload size is bounded per file but there is no total disk quota or malware scan. The AI base URL is configurable and not restricted to public hosts. There are no outbound request allowlists, per-user authorization model, or documented restore drill. Do not expose local mode beyond loopback.

## Indexes, query patterns and contention

Existing indexes: task `due_at`, run `created_at DESC`, and unique node `(run_id,node_id)` (created during initialization). Unique constraints also cover primary keys and scheduled occurrence keys. The UI bootstrap reads bounded recent task/session/document/run/notification lists, but workflow and agent lists are unbounded. Run detail filters `node_runs` by run id and sorts by rowid. The scheduler currently scans every open task and every enabled schedule every five seconds, then parses timestamps in Python; at larger record counts this is the clearest repeated-work candidate. SQLite's single-writer lock and multiple short-lived connections are the likely contention point under concurrent nodes; no contention measurement has yet been recorded. `EXPLAIN QUERY PLAN` measurements are included in the benchmark command, not inferred as throughput results.

## Caching and request efficiency

No application cache exists. This is correct for authoritative run state, schedules, approvals and notifications. Immutable workflow definitions are loaded once at run start. The UI polls the full bootstrap payload every ten seconds and debounces search; it can issue duplicate bootstrap reads after mutations. No response cache or provider result cache is currently used. AI result caching is not introduced because prompt/model/version/freshness semantics and private-data retention need explicit policy and measurement.

## Test coverage and missing failure-path coverage

The existing suite covers task/workflow HTTP lifecycles, manual approval/cancel, DAG validation, dependency outputs, schema mismatch, reminder/schedule recurrence and deduplication, interrupted-run startup recovery, document upload/delete, and a mocked successful summary call. Frontend tests cover task creation/completion/reminder entry and the access-token gate. The suite did not previously cover transient provider retry, exhausted retries, permanent provider errors, node timeout classification, mid-attempt cancellation, duplicate run launch, malformed/oversized JSON output, secret redaction, or measured concurrency contention. New reliability changes add focused tests for the most relevant cases; no live provider credentials are used.

## Measurements

No production performance measurement is available. The reproducible `backend/benchmarks/workflow_benchmark.py` harness reports sample count, warm-up, p50/p95 workflow latency, throughput, failures, and node latency for local transform workflows; it also prints scheduler query plans. It does not simulate real provider latency or estimate provider cost. Results are environment-specific and should not be treated as production capacity. The benchmark and exact run output are recorded after the reliability changes below.

## Prioritized gaps

| Priority | Severity | Evidence | Recommended fix | Verification |
| --- | --- | --- | --- | --- |
| 1 | High | Every node exception is terminal and stored as an undifferentiated string; timeouts are generic failures. | Bounded retry only for transient network/timeout/429/5xx failures; persist category, reason, attempt duration and count; preserve cancellation. | Deterministic fake-provider tests for retry, permanent failure, timeout, cancellation, and dependent skipping. |
| 2 | High | Run creation and executor status updates are not consistently guarded by state predicates; in-process map is the only active-run registry. | Atomic conditional state claims in SQLite; reject duplicate active transitions; document single-process deployment boundary. | Concurrent request test and state-transition race test. |
| 3 | High | Crash after remote success before result commit can cause repeated operation/cost. | Document at-least-once semantics; use provider idempotency keys or reconciliation for future side effects. | Crash-window design review; provider-specific idempotency test when an integration supports it. |
| 4 | Medium | No request/run/node correlation or bounded metrics; logs can be plain text and errors may include validation values. | Bounded internal metrics, structured logs, request/run/node/attempt IDs, sanitized error mapping. | Capture logs with sentinel secret/prompt and assert absent; check counters and bounded storage. |
| 5 | Medium | Scheduler queries all open tasks and enabled schedules on each five-second poll; no indexes for status and queue filters. | Limit selection to due records and add measured/useful composite indexes. | SQLite query plan and scheduler scale benchmark before/after. |
| 6 | Medium | Each AI/document/GitHub operation creates a new HTTP client. | Consider shared connection pooling after benchmark and lifecycle tests. | Simulated repeated provider workload, compare connection count/latency. |
| 7 | Medium | Uploads have per-file size limit but no total storage quota; AI base URL can be arbitrary. | Add configurable aggregate limits and optional outbound host policy for remote deployments. | Disk-quota and private-network URL rejection tests. |

## Post-audit implementation status

Changes made after writing this baseline audit, evidence, benchmark output, and decision records are summarized in [project status](../PROJECT_STATUS.md) and the [decision records](decisions/). In particular, bounded retries, execution/idempotency claims, HTTP pooling, request correlation, and safe process-local metrics now have implementation and test coverage. Remaining limitations are called out there and in the current architecture/deployment documentation rather than described as solved.
