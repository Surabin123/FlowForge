# FlowForge project status

Updated: 2026-10-24

## Audit

- Existing implementation: React/TypeScript/Vite interface; FastAPI with SQLite; task/session/document/workflow/agent records; graph validation, dependency execution, manual approval, cancellation/retry, and GitHub read-only API.
- Gaps: task reminder fields had no scheduler or delivery; summaries were a placeholder; workflow schedules were absent; interrupted runs could remain `running`; CI and release materials were missing.
- Data stays local by default. API is loopback-only and has no authentication; do not expose it remotely.

## Phase 1 — Product UI

- Connected notification count/history and workflow schedule controls to persisted application data. Added user confirmation before document text is sent to the configured external AI provider.
- Files: `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/App.test.tsx`.
- Checks: frontend tests passed (3); TypeScript/Vite production build passed.
- Remaining: screenshots could not be captured because local browser use is disabled in the desktop environment. Frontend journey coverage now includes task creation/completion and reminder setup.

## Phase 2 — Reminders and scheduling

- Added an SQLite-polled scheduler that survives restarts, persists due-date and reminder alerts, deduplicates delivery, supports daily/weekdays/weekly recurrence in the configured IANA timezone, and persists workflow occurrences before execution.
- Workflow schedules are one-time or recurring and can be paused/resumed. Startup recovery marks interrupted running records failed with a retry note and audit event.
- Files: `backend/app/database.py`, `backend/app/main.py`, `backend/app/scheduler.py`, `backend/app/schemas.py`, tests.
- Checks: full backend suite passed (19 tests), including recurrence, timezone/DST, notification dedupe, schedule dedupe, and interruption recovery.
- Limits: in-app delivery only; local API process must be running. This is single-process recovery, not a durable distributed queue.

## Phase 3 — Document summaries

- Added provider-backed summaries using existing OpenAI-compatible chat API settings, stored status/error/timestamp, size and response checks, and a confirmation before external processing.
- Original files remain unchanged; no summary is fabricated if configuration is missing or requests fail.
- Files: `backend/app/main.py`, `backend/app/database.py`, `frontend/src/App.tsx`, `backend/tests/test_summary.py`.
- Checks: mocked provider test passed in the backend suite. A live provider request requires credentials and has not been exercised.

## Phase 4 — Reliability and release engineering

- Added GitHub credential/rate-limit diagnostics and successful-refresh timestamp. GitHub actions remain read-only.
- Added `.github/workflows/ci.yml`, `docs/ARCHITECTURE.md`, and a rewritten developer README. Backend suite passed (19 tests); frontend suite passed (3 tests); TypeScript/Vite production build and Python compile checks passed.
- A local startup smoke test returned API health `ok` and UI HTTP 200. Browser automation returned “Local browser use is disabled in Work settings”; no screenshot was fabricated.
- Source and placeholder configuration scan found no credential patterns; `.env`, databases, uploads, caches, and generated artifacts are ignored.
- Created a clean local Git repository on `main` and committed the reviewed source as `1995d1240b5225840da48f1c043f212bb1dca9e8`; configured `origin` to the requested URL. Working tree is clean.
- GitHub public lookup returned “Repository not found”; this cannot distinguish a private repo from an absent repo. No `gh` CLI or Git credential helper/token was available. The unauthenticated remote lookup cannot establish remote state or publish.

## Next work

Remaining release work: capture screenshots when local browser access is available; authenticate GitHub to verify access to the existing repository, then push only after review. Repository creation and publication were not performed.

## CI and Render preparation — 2026-10-20

- Restricted GitHub Actions to pushes and pull requests targeting `main`; the workflow runs backend tests, Python compilation, frontend tests, an optional configured lint script, TypeScript validation, and the Vite production build with cached dependencies and read-only repository permissions.
- Added a multi-stage Python 3.12/Node 22 Docker build and `render.yaml` for one combined web service. The frontend bundle is served by FastAPI. Render is configured to deploy commits from `main`, use `/api/health`, and store SQLite and uploaded originals on `/var/data`.
- Added optional API bearer-token protection and a sign-in screen. Local default remains local-only with auth off; Render sets auth on and generates the token. Added an SQLite online-backup utility and Render setup, restart, backup, and rollback documentation.
- Local checks: Python compile passed; backend suite passed (21 tests); frontend suite passed (4 tests); TypeScript/Vite build passed; optional lint step completed successfully, with no lint script configured. The Render YAML parsed successfully. Docker image build could not run because the Docker Desktop Linux engine is not running. No GitHub Actions run, Render account connection, remote build, or live deploy has been performed. The Render blueprint requires a paid web service and persistent disk; multi-instance scheduling and off-site backup automation are not implemented.
- Remaining account steps: push the reviewed commit to the existing repository, connect it to Render, approve the paid plan/disk, retrieve the generated token, and perform HTTPS login and persistence smoke checks.

## Execution reliability and engineering baseline — 2026-10-20

- Completed a source-based engineering audit and recorded decisions for modular-monolith boundaries, SQLite/scheduler trade-offs, bounded retry and idempotency semantics, cache policy, and internal telemetry. See `docs/ENGINEERING_AUDIT.md` and `docs/decisions/`.
- Workflow node attempts now persist durations, safe failure categories/reasons, and bounded attempt histories. Transient provider/network failures and timeouts retry with exponential jitter under a maximum attempt limit; permanent configuration/validation errors stop. Run execution and node transitions use conditional SQLite claims; manual run creation accepts an idempotency key; run starts are admission limited. Restart/cancellation paths preserve interruption information and explain external reconciliation.
- AI, summary, and GitHub HTTP calls use a shared bounded async client pool during app lifespan. Requests are measured without logging URLs, headers, prompts, bodies, or response content. Added request IDs, structured allowlisted logs, fixed-cardinality bounded metrics and an internal `/api/internal/metrics` endpoint. Added query indexes and due-time filtering for the persistent scheduler.
- Added controlled limits for workflow inputs/outputs and concurrency, backend reliability tests, and a repeatable synthetic benchmark with 100 samples per workload. The Windows 11/Python 3.12.14/SQLite 3.53.1 baseline measured p95 120.7 ms for 5-node runs, 629.7 ms for 40-node graphs, 64.8 ms for synthetic 64 KB TXT processing plus workflow serialization, 129.3 ms for a simulated transient provider, and 578.0 ms for four concurrent 10-node runs. Empty-due scheduler poll p95 was 1.72 ms. All benchmark workloads completed with zero failures. Full environment and limits are in `docs/benchmarks/README.md` and `docs/benchmarks/local-baseline.json`.
- Validation: `compileall` passed; backend suite passed (30 tests); frontend suite passed (4 tests); TypeScript/Vite production build passed; `git diff --check` passed apart from Git's Windows line-ending notices and one trailing blank line subsequently removed. The backend tests and benchmark required escalated local socket access because of Windows asyncio sandbox restrictions. No live AI/GitHub calls were made.
- Limits: benchmark data is synthetic/local and does not establish deployment capacity. Metrics are in-process, reset at restart, and have no exporter. CPU is process-level; peak RSS was unavailable on this Windows runtime. SQLite is still a single-writer store; run history retention is not implemented. External side effects remain at-least-once across crash windows. There is no provider cost estimator or result cache; the cache decision is deferred pending a measured repeated-read benefit and explicit freshness/privacy semantics. No distributed worker queue, multi-instance scheduler lease, Kubernetes autoscaling, or production observability claim is made.
- No commit, push, or deployment was performed in this engineering increment. Earlier CI/Render preparation remains local and unverified against remote services.

## Personal AI automation extension — 2026-10-24

- Audited the existing app before extension in `docs/AUTOMATION_AUDIT.md`. Reused the React shell, FastAPI API, SQLite records, AI outbound client, workflow DAG engine, scheduler, run history, approval gates, and notification/document services.
- Added strict natural-language planning through the configured OpenAI-compatible provider, typed server validation, clarification state, editable proposal and schedule, content-hash approval, saved workflow creation, manual execution, and durable artifacts. The Automations page shows steps, permissions, side effects, current run progress, verified results, and actionable errors.
- Added 14 registered tools for actual local task/document/study/notification/run reads and writes, daily/weekly plans, document AI summary/date candidates, and artifact storage. Task creation and effect logging commit together; artifacts and notifications have run/node deduplication. Tool dispatch verifies the approved run snapshot and exact parameters. Direct workflow CRUD cannot inject tool nodes. Whole-run retry blocks attempted writes and external calls.
- Added a 300-second default workflow execution-segment cap, strict tool input/output schemas, maximum 12 planned steps, and verified/candidate-only output labels. Document instructions are treated as untrusted data; extracted deadlines are candidates and never create tasks automatically.
- Tests: backend suite 37 passed after this extension (one upstream Starlette/httpx deprecation warning); frontend Vitest 7 passed; TypeScript/Vite production build passed; Python compileall and `git diff --check` passed. The end-to-end backend test uses a deterministic mocked planner, then reads real stored tasks, orders them, saves an artifact, and verifies persistence. Security/failure tests cover unsafe model tools, malformed and oversized output, missing credentials, approval mismatch/edit invalidation, duplicate schedule fire, write replay prevention, runtime parameter tampering, and document prompt injection confinement. No live provider or deployed service was tested.
- Configuration: `FLOWFORGE_AI_API_KEY` and `FLOWFORGE_AI_MODEL` are required for new natural-language proposals and document AI tools. Local saved automations with non-AI tools can run without another planning request. `tzdata` was added to Python requirements for IANA timezone support on Windows.
- Remaining unsupported: arbitrary HTTP/API integration, external event triggers, email/calendar/GitHub writes, automatic task creation from document candidates, nested workflow execution, partial-run resume after uncertain external effects, durable multi-process scheduling, cost estimation without provider pricing, and live AI/provider verification. These are not advertised as completed capabilities. No commit, push, or deployment was performed; earlier uncommitted work was preserved.
