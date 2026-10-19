# Architecture and operations

FlowForge is a loopback-only single-user application. React + TypeScript/Vite sends requests to FastAPI; FastAPI stores application state in SQLite and uploaded originals on the local filesystem. The API process owns an async workflow engine and a five-second polling scheduler.

## Persistent scheduling

- Times with an explicit UTC offset are stored as ISO 8601 strings. New task reminders accept `daily`, `weekdays`, `weekly`, or no recurrence. Recurrences retain local wall time using the saved IANA workspace timezone, including daylight-saving transitions.
- Due-date alerts and timed reminders are stored in `notifications`. Unique occurrence keys prevent duplicate delivery. In-app alerts are visible in task/reminder history.
- Workflow schedules persist the next execution, recurrence, timezone, last execution, enable state, and an error field in `workflow_schedules`. `scheduled_fires` claims each due occurrence before a `queued` run is written. The scheduler picks queued runs up after restart.
- On startup, `running` workflow and node records are marked failed with an audit event. A mid-node resume is not supported; a user can inspect and retry the workflow.

## Document processing

Supported uploads are size-limited PDF, TXT, Markdown, and CSV. Extraction is local; the original file is retained. Summary requests use the configured OpenAI-compatible `/chat/completions` endpoint, with a 100,000 character input limit, timeout, response validation, and persisted state. The UI asks before transmission. Provider response bodies and API keys are not returned to the browser.

## Operational boundaries

No authentication is provided. Keep `FLOWFORGE_HOST=127.0.0.1` (the run script uses loopback) and do not expose the API to other machines. There is no durable external queue, multi-process scheduler lock, distributed worker, HA, metrics export, trace collection, push notification service, or production deployment. For concurrent multiple API processes, the local scheduler would need a cross-process lease or a separate job system before use.

## CI

`.github/workflows/ci.yml` installs Python requirements and uses `npm ci` from the committed lockfile, then runs backend tests, frontend tests, and a production build. Tests do not require secrets or external providers; AI provider tests use an HTTP mock.
