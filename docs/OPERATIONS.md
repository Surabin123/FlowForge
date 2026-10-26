# Local setup and operations

This guide contains the setup, configuration, and troubleshooting detail kept out of the [repository landing page](../README.md). FlowForge is a single-user application. Local development binds to `127.0.0.1` and does not require a login by default.

## Requirements and local startup

- Python 3.11 or later; CI uses Python 3.12.
- Node.js 20.19+ or 22.12+ and npm; CI uses Node 22.
- Free local ports 8000 (API) and 5173 (UI).

On Windows PowerShell, run `powershell -ExecutionPolicy Bypass -File .\run.ps1` from the repository root. The launcher copies `.env.example` to `.env` when missing, creates `.venv`, installs Python requirements, runs `npm ci` when frontend dependencies are absent, and starts both local services. Stop it with Ctrl+C.

For separate terminals on any platform:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
npm --prefix frontend ci
```

Use `Copy-Item .env.example .env` on Windows if your shell does not provide `cp`. Start the API in one terminal and the UI in another:

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173`; `http://127.0.0.1:8000/api/health` is the API health endpoint. The Vite development server proxies API requests to FastAPI.

## Environment configuration

All settings use the `FLOWFORGE_` prefix. Copy `.env.example`, then edit the ignored `.env` file. Empty optional credentials disable the corresponding integration. Do not commit `.env` or paste tokens into workflow descriptions.

| Variable | Default in `.env.example` | Purpose |
| --- | --- | --- |
| `FLOWFORGE_DATABASE` | `data/flowforge.db` | SQLite file path |
| `FLOWFORGE_DOCUMENTS` | `storage/documents` | Original upload directory |
| `FLOWFORGE_TIMEZONE` | `Asia/Calcutta` | IANA timezone for recurring reminders and schedules |
| `FLOWFORGE_MAX_UPLOAD_MB` | `20` | Per-upload limit |
| `FLOWFORGE_MAX_PARALLEL_NODES` | `4` | Concurrent nodes per workflow |
| `FLOWFORGE_MAX_PARALLEL_RUNS` | `4` | In-process run admission limit |
| `FLOWFORGE_MAX_WORKFLOW_DURATION_SECONDS` | `300` | Maximum execution segment duration |
| `FLOWFORGE_WORKFLOW_MAX_ATTEMPTS` | `3` | Upper bound for eligible node attempts |
| `FLOWFORGE_WORKFLOW_RETRY_BASE_SECONDS` | `0.25` | Initial retry delay |
| `FLOWFORGE_WORKFLOW_RETRY_CAP_SECONDS` | `2` | Retry delay cap |
| `FLOWFORGE_MAX_WORKFLOW_OUTPUT_BYTES` | `1000000` | Serialized workflow node output cap |
| `FLOWFORGE_MAX_AI_INPUT_CHARS` | `100000` | AI prompt input cap |
| `FLOWFORGE_AI_API_KEY` | empty | Key for the configured compatible provider |
| `FLOWFORGE_AI_BASE_URL` | `https://api.openai.com/v1` | Provider API base URL |
| `FLOWFORGE_AI_MODEL` | empty | Provider model for planning and document analysis |
| `FLOWFORGE_GITHUB_TOKEN` | empty | Optional read-only developer activity token |
| `FLOWFORGE_GITHUB_OWNER` | empty | Owner for optional developer activity |
| `FLOWFORGE_REQUIRE_AUTH` | `false` | Require an access token for non-local exposure |
| `FLOWFORGE_ACCESS_TOKEN` | empty | Secret used when the access gate is enabled |
| `FLOWFORGE_ALLOWED_HOSTS` | `127.0.0.1,localhost,testserver` | Accepted hostnames for API requests |
| `FLOWFORGE_CONTAINER_MODE` | `false` locally; `true` in the Docker image | Fail startup unless auth, explicit hosts, and absolute storage paths are configured |

The application validates numeric ranges in `backend/app/config.py`. New natural-language proposals, summaries, and document deadline candidates need both the AI key and model. Core local records and saved non-AI workflows work without them. When an AI feature is used, its prompt or selected document text goes to the configured provider; review that provider's data policy.

## Storage, authentication, and backup

SQLite and uploaded originals stay under `data/` and `storage/documents/` by default. These paths, `.env`, virtual environments, test caches, and frontend build artifacts are ignored by Git. No sample records are inserted at startup.

The default launcher and manual commands bind to loopback. The Docker image binds externally only after container-mode startup validation: auth must be enabled, the token must contain at least 32 characters, trusted hosts cannot be empty or `*`, and database/upload paths must be absolute. If deliberately exposing FlowForge remotely, also use HTTPS and a persistent volume for both paths. The [Render guide](DEPLOYMENT_RENDER.md) details its paid disk prerequisite, secret handling, manual deployment, backup, and rollback. The included deployment configuration is preparation; a live deployment has not been verified.

Use the [SQLite online-backup utility](DEPLOYMENT_RENDER.md#sqlite-backup-restart-and-rollback-limits) for consistent database copies. A backup on the same disk is not an off-site backup. Protect exported files because they contain personal data.

## Tests and build

From the repository root with Python dependencies installed:

```bash
python -m pytest backend/tests -q
python -m compileall -q backend/app
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Build the frontend before the browser smoke test, and install Playwright Chromium or use a locally installed Edge browser on Windows. GitHub Actions runs backend tests, Python compilation, frontend tests, real lint, the production build, and the browser smoke test for pushes and pull requests targeting `main`. Provider tests use mocks and require no external credentials. The [engineering audit](ENGINEERING_AUDIT.md) and [synthetic benchmark](benchmarks/README.md) explain what was measured locally; they do not establish hosted performance.

## Failure and schedule recovery

The scheduler polls persisted due work while the API process is running. It uses occurrence keys to prevent duplicate scheduled fires under the supported single-process design. After restart, queued work is picked up; interrupted running nodes are marked failed with recorded reasons. No node is resumed mid-operation.

Inspect run history and side-effect outcomes before retrying. A run that attempted a local write or external request is blocked from blind whole-run replay because a process crash can leave the outcome uncertain. Read-only transient failures have bounded retry rules. FlowForge does not provide a distributed worker queue, multi-process scheduler lease, high availability, or automatic off-site backup.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| UI cannot reach API | Confirm ports 5173 and 8000 are free and `/api/health` returns `ok`. |
| Planning or summary unavailable | Set the AI key and model in `.env`, verify the base URL, and restart the API. |
| Upload rejected | Use PDF, TXT, Markdown, or CSV and check `FLOWFORGE_MAX_UPLOAD_MB`. |
| GitHub activity unavailable | Check the owner, read access on the token, and provider rate-limit response. |
| Scheduled work did not run | Keep the API process running; inspect whether the workflow schedule is enabled and its next due time. |
| Interrupted run failed | Inspect saved node outcomes and reconcile any attempted effects before starting new work. |

For product behavior and examples, see the [feature walkthrough](USAGE.md). For implementation boundaries, see [architecture](ARCHITECTURE.md).
