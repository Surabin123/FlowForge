# Architecture and operations

FlowForge is a single-user application. In local development, React + TypeScript/Vite sends requests to FastAPI; FastAPI stores application state in SQLite and uploaded originals on the local filesystem. The API process owns an async workflow engine and a five-second polling scheduler. The optional Render container serves the compiled Vite app from FastAPI and stores SQLite plus uploads on one persistent disk.

## Personal automation path

`frontend/src/AutomationPage.tsx` uses the existing application shell and API client. `backend/app/automation.py` sends one bounded structured-planning request through the existing outbound HTTP pool, parses a strict proposal, validates a maximum of 12 steps with the existing DAG validator, and validates each typed tool input through `backend/app/tool_registry.py`. A proposal needing clarification has no executable steps. Missing provider credentials return a configuration error; no local fallback pretends to be an AI plan.

The proposal and model/prompt version/token usage (when reported) are stored in `automation_plans`. Editing resets approval. Approval inserts an `automation_approvals` row with a SHA-256 hash of the exact title, summary, nodes, edges, and schedule. Saving copies the approved definition into the existing `workflows` table and, when requested, inserts an existing `workflow_schedules` row. A run snapshots the definition in `runs`; the engine verifies the approval hash before running a tool, and the tool dispatcher checks that its selected node and parameters match that snapshot. Direct workflow CRUD rejects tool nodes, so it cannot bypass the approval path. Saved definitions cannot be edited in place; a changed future automation needs a new proposal and approval while old run history remains intact.

The registry provides versioned Pydantic input/output schemas, permission and side-effect labels, timeouts, output caps, retry and idempotency policies. Tools read or write existing task, document, study, notification, workflow, and artifact records. Local task creation and its `(run_id,node_id)` effect ledger commit in one SQLite transaction; a repeated dispatch reads back the first task. Artifact writes have a unique run/node key. Completion and notification operations are idempotent against their local state. Outputs carry `verification: verified` only after read-back or validation; deadline extraction carries `candidate_only`. This indicates validated candidates, not a verified real-world deadline or created task.

Plan states are `needs_clarification`, `awaiting_approval`, `approved`, or `cancelled`. Execution uses the existing `queued` → `running` → `completed`/`failed`/`cancelled` run model, with `paused` for manual gates. Scheduled occurrences use the existing durable claim and duplicate protection; a process restart reclaims queued work and marks interrupted running work failed. Tool workflows with an attempted write or external request cannot be retried as a whole through the retry endpoint, since the prior effect may be uncertain. Existing read-only retry rules remain bounded. The engine also caps each execution segment by `FLOWFORGE_MAX_WORKFLOW_DURATION_SECONDS`; this is not a durable cross-restart deadline.

The model never receives a shell, Python, SQL, arbitrary HTTP, or nested-workflow execution tool. Document contents are sent as untrusted user data to the deadline extractor under a fixed system instruction, and only a validated candidate schema is accepted. Approval is from the user, never document or model text. There is no general HTTP tool yet: preventing SSRF across DNS resolution and redirects, binding credentials outside definitions, and validating external outcomes are prerequisites. No external connector or live AI result has been verified.

Planning is one model call and graph validation is O(V + E) for at most 12 steps. Scheduler polling is bounded by due-time indexes and its configured batch limit; it is a single-process SQLite scheduler. Read outputs are not cached because tasks and documents are mutable. Approved plan reuse avoids repeated planning calls. Provider token counts are recorded only when supplied; cost is not estimated without a trusted price source. The existing synthetic engine benchmark is described in `docs/benchmarks/README.md`; it does not measure live AI or these new tool paths.

## Persistent scheduling

- Times with an explicit UTC offset are stored as ISO 8601 strings. New task reminders accept `daily`, `weekdays`, `weekly`, or no recurrence. Recurrences retain local wall time using the saved IANA workspace timezone, including daylight-saving transitions.
- Due-date alerts and timed reminders are stored in `notifications`. Unique occurrence keys prevent duplicate delivery. In-app alerts are visible in task/reminder history.
- Workflow schedules persist the next execution, recurrence, timezone, last execution, enable state, and an error field in `workflow_schedules`. `scheduled_fires` claims each due occurrence before a `queued` run is written. The scheduler picks queued runs up after restart.
- On startup, `running` workflow and node records are marked failed with an audit event. A mid-node resume is not supported; a user can inspect and retry the workflow.

## Document processing

Supported uploads are size-limited PDF, TXT, Markdown, and CSV. Extraction is local; the original file is retained. Summary requests use the configured OpenAI-compatible `/chat/completions` endpoint, with a 100,000 character input limit, timeout, response validation, and persisted state. The UI asks before transmission. Provider response bodies and API keys are not returned to the browser.

## Workflow reliability and diagnostics

Each node start is claimed with a conditional SQLite state update. A run has a persisted execution claim, and optional `Idempotency-Key` values deduplicate manual starts per workflow. Transient provider/network failures and timeouts use bounded retries with exponential jitter; configuration, schema, and most client failures stop immediately. Attempts, durations, categories, safe reasons, and bounded history are persisted. `FLOWFORGE_MAX_PARALLEL_RUNS` limits scheduled and manual execution admission in this single process.

External effects are at-least-once across the crash window between provider success and local result commit. Restart recovery marks active attempts interrupted and asks the operator to reconcile before retrying. It cannot resume an in-flight node or guarantee exactly-once provider behavior. JSON logs carry request IDs and allowlisted metadata. `/api/internal/metrics` exposes bounded process-local counters and percentiles; values reset on process restart, and no exporter or trace backend is configured. See `docs/ENGINEERING_AUDIT.md` for the audit and `docs/benchmarks/README.md` for the synthetic local baseline.

## Operational boundaries

Local development defaults to no authentication and the run script binds to loopback. The Render blueprint sets `FLOWFORGE_REQUIRE_AUTH=true`, generates `FLOWFORGE_ACCESS_TOKEN`, and protects every API route except `/api/health` and the token handshake. Do not set the auth requirement without supplying a secret token. There is no durable external queue, multi-process scheduler lock, distributed worker, HA, metrics export, trace collection, or verified remote deployment. For concurrent multiple API processes, the scheduler would need a cross-process lease or a separate job system before use.

## CI

`.github/workflows/ci.yml` installs Python requirements and uses `npm ci` from the committed lockfile, then runs backend tests, frontend tests, and a production build. Tests do not require secrets or external providers; AI provider tests use an HTTP mock.

The deployment and recovery plan is documented in `docs/DEPLOYMENT_RENDER.md`. The Render disk is paid and single-instance. SQLite online backups can be created with `python -m app.backup_database <destination>` (with `backend` on `PYTHONPATH`); off-site retention and restore verification remain operator tasks.
