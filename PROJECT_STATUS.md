# FlowForge project status

Updated: 2026-10-19

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

Remaining release work: capture screenshots when local browser access is available; authenticate GitHub, check whether the requested repository exists privately, create it if absent, then push the reviewed local commit. No remote publication is claimed.
