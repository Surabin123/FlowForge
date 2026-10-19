# FlowForge

FlowForge is a private, local-first workspace for personal tasks, study planning, documents, and small AI-assisted workflows. It brings daily planning and local automation into one single-user app while keeping records and uploaded files on the machine by default.

## Capabilities

- **Tasks and reminders:** searchable task CRUD, priority and due dates, one-time alerts, daily/weekday/weekly recurrence, and persistent in-app notification history.
- **Study planner:** subjects, planned sessions, completion records, and weekly scheduled-minute totals.
- **Documents:** PDF, TXT, Markdown, and CSV uploads, locally extracted text, search, and preserved originals.
- **AI summaries:** send extracted text to a configured OpenAI-compatible provider after an explicit confirmation. Summary status and errors are saved locally. AI configuration is optional.
- **Workflows:** saved dependency graphs, transform and AI nodes, manual approval, execution history, cancellation/retry, and one-time or recurring schedules.
- **AI agents:** reusable instruction and JSON schema definitions assignable to workflow nodes.
- **Developer hub:** read-only GitHub repository, pull request, commit, and Actions activity when a token is configured.

The interface is designed around operational workspace views and uses the SQLite-backed application API. The app does not create sample records on startup.

## Screenshots

No screenshots are included yet. The desktop environment used for this build disables local browser automation, so I could not capture and inspect genuine running-app screenshots. Start the app locally and capture the Overview, Workflows, and Execution History pages before publishing screenshots.

## Architecture

```mermaid
flowchart LR
  UI[React + TypeScript + Vite] -->|HTTP on loopback| API[FastAPI]
  API --> DB[(SQLite)]
  API --> Files[Local document storage]
  Scheduler[SQLite polling scheduler] --> DB
  Scheduler --> Engine[Async workflow engine]
  Engine --> DB
  API -. optional, configured .env .-> AI[OpenAI-compatible provider]
  API -. read-only, optional .-> GH[GitHub REST API]
```

The API binds to loopback. The scheduler checks persisted due work every five seconds and uses unique occurrence keys for notification and scheduled-run deduplication. SQLite UTC timestamps are converted for recurring reminders in the configured IANA timezone. On API startup, runs left `running` by a prior process are marked failed with an audit entry; queued scheduled runs are picked up by the scheduler. This is a single-process local design, not a distributed queue.

## Stack

- React 19, TypeScript, Vite, Lucide, custom responsive CSS
- Python 3.11+, FastAPI, Pydantic Settings, Uvicorn
- SQLite, local filesystem document storage, `pypdf`, `jsonschema`
- Vitest and React Testing Library; pytest and FastAPI TestClient

## Windows setup

Requirements: Windows PowerShell, Python 3.11 or later, Node.js 20 or later, and npm.

```powershell
Set-Location D:\FlowForge
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The API health endpoint is [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health). The script creates `.env` from the placeholder `.env.example`, installs Python and frontend dependencies when needed, and starts both local services.

For separate terminals:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
npm --prefix frontend ci
npm --prefix frontend run dev
```

## Configuration

Copy `.env.example` to `.env`. Keep real credentials in `.env`; it is ignored by Git.

| Variable | Purpose |
| --- | --- |
| `FLOWFORGE_DATABASE` | SQLite path; default `data/flowforge.db` |
| `FLOWFORGE_DOCUMENTS` | Original upload directory; default `storage/documents` |
| `FLOWFORGE_TIMEZONE` | IANA timezone for recurring reminders; default `Asia/Calcutta` |
| `FLOWFORGE_MAX_UPLOAD_MB` | Upload limit; default 20 MB |
| `FLOWFORGE_MAX_PARALLEL_NODES` | Workflow concurrency guard |
| `FLOWFORGE_AI_API_KEY`, `FLOWFORGE_AI_BASE_URL`, `FLOWFORGE_AI_MODEL` | Optional OpenAI-compatible provider |
| `FLOWFORGE_GITHUB_TOKEN`, `FLOWFORGE_GITHUB_OWNER` | Optional read-only GitHub activity |

AI summaries send the selected document's extracted text to the configured provider only after the app displays a confirmation. Do not use the feature unless the provider's data handling is acceptable for that document. Without AI credentials, task, study, upload, search, transform workflow, and other local features remain available.

## Tests and build

```powershell
python -m pytest backend/tests
npm --prefix frontend test
npm --prefix frontend run build
```

GitHub Actions runs these backend tests, frontend tests, and the TypeScript/Vite production build on pushes and pull requests.

## Security and operating limits

- This is a local development application. It has no login and must not be exposed to a network. Keep the API bound to `127.0.0.1`.
- GitHub integration is read-only. It does not commit code, merge PRs, trigger Actions, or deploy.
- AI requests leave the local machine only when the user confirms a summary or runs a configured AI workflow node.
- Original documents and the SQLite database are local and excluded from Git. Upload type and size restrictions are enforced; malware scanning is not implemented.
- In-app notifications are stored locally. Email, SMS, mobile push, and OS notifications are not implemented.
- The scheduler runs in the API process. It recovers persisted scheduled work after a restart, but there are no distributed workers, HA, metrics exporter, OpenTelemetry, Kubernetes autoscaling, or production deployment.
- A process restart marks interrupted workflow runs failed so they can be inspected and retried; it does not resume a node mid-operation.

## Troubleshooting

- **UI cannot reach API:** check `/api/health` and make sure ports 8000 and 5173 are available.
- **Upload rejected:** supported formats are PDF, TXT, Markdown, and CSV; check `FLOWFORGE_MAX_UPLOAD_MB`.
- **Summary unavailable:** configure `FLOWFORGE_AI_API_KEY` and `FLOWFORGE_AI_MODEL`, restart the API, and confirm the data-sharing prompt for the document.
- **GitHub unavailable:** check the token's read-only repository access and configured owner; rate limits and invalid credentials are reported without returning the token.
- **Interrupted run:** restart recovery marks the run failed with a retry note; inspect its saved node outcomes before retrying.
