# Automation extension audit (2026-10-24)

This inventory was made before the automation extension. It describes the existing checkout, including uncommitted reliability and Render preparation work. It is a source and local-test audit, not a live provider or deployment verification.

| Area | Existing implementation to reuse | Missing for a personal assistant |
| --- | --- | --- |
| UI | One React/Vite shell with workflow editor, schedules, run history, tasks, study, documents, agents, and auth screen | Natural-language composer, plan review/edit/approval, precise tool permissions and verification presentation |
| API | FastAPI routes for CRUD, workflows, run actions, summaries, GitHub read-only, settings | Plan lifecycle endpoints, registered tool catalog and server validation |
| Persistence | SQLite for tasks, documents, notifications, workflows, schedules, runs, node attempts, audit log; local upload files | Plan/approval records, generated artifacts, local tool effect ledger |
| Executor | DAG validation, dependent nodes, transform/agent/manual nodes, retries, timeouts, cancellation, run claim, startup interruption handling | Typed tool nodes, per-tool retry/idempotency rules, verified outcomes and approval binding |
| Scheduler | One in-process five-second loop with durable schedule rows and occurrence deduplication | No new scheduler needed; approved automations can save into existing workflow/schedule tables |
| AI | Configured OpenAI-compatible `/chat/completions` via bounded shared outbound client; usage counters | Strict structured planner, tool selection validation, prompt version, explicit missing-credential response |
| Security | Optional API bearer token, generated upload paths, disabled arbitrary HTTP workflow node, output/input limits | Tool permission enforcement, plan hash approval, untrusted-content boundary, external HTTP allowlist design |
| CI/tests | Backend pytest, frontend Vitest, compile/build in CI; 30 backend and four frontend tests in prior pass | End-to-end plan/tool tests, malicious plan tests, approval invalidation, verification and frontend lifecycle tests |

The first complete path will use real stored tasks and generated artifacts: model proposal → registry and DAG validation → exact plan approval → saved workflow → tool execution → database verification → run detail. Existing scheduling will run the same approved workflow definition. Unsupported external connectors will be reported as unavailable until they have a safe implementation and credentials.
