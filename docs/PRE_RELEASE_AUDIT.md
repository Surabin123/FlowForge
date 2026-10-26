# FlowForge pre-release audit

**Date:** 2026-10-26 (Asia/Calcutta)
**Verdict:** **Conditionally ready to consider for source publication; remote deployment remains unverified.** The local tests, real lint gate, browser smoke test, and dependency audits now pass. The Docker image has a fail-closed startup policy, but no image build or live deployment was possible here. The unresolved risks and publication checklist below still require review before any publishing action.

## 1. Git state and protection of existing work

- Branch: `main`, tracking `origin/main`. Local tracking comparison: 0 ahead, 0 behind; this audit did **not** fetch, so it cannot establish the live remote state.
- Configured remote: `origin` at `https://github.com/Surabin123/FlowForge.git` for fetch and push. No remote configuration was changed.
- Before audit edits: modified `README.md`, `docs/ARCHITECTURE.md`, `docs/ENGINEERING_AUDIT.md`, `docs/USAGE.md`, `docs/assets/flowforge-banner.svg`; untracked `docs/OPERATIONS.md`. The complete working diff and untracked guide were read. Nothing was staged.
- Audit edits added locally: `.gitignore`, `.dockerignore`, `Dockerfile`, `render.yaml`, `requirements.txt`, CI, backend configuration/API/test files, frontend lint/browser files and tests, documentation, and this report. No pre-existing changes were discarded. The final working tree remains unstaged.
- Four local commits were inspected by path and blob for secret scanning. No commit, push, tag, release, deployment, remote mutation, reset, or stash was performed.

## 2. Secrets, sensitive files, and ignore rules

**Passed with scanner limitation:** A local signature scan of 59 tracked paths, 6 untracked paths, and 79 unique Git-history blobs across four commits found no matching private-key markers, AWS/GitHub/OpenAI-style tokens, non-placeholder credential assignments, or sensitive tracked filenames. The staged diff is empty. It did not read ignored local runtime files or prove that arbitrary credential formats are absent. Gitleaks, TruffleHog, and detect-secrets were unavailable; the ignored local scanner is `.tmp/audit_scan.py`. No secret values were printed.

`.env.example` contains empty credentials, safe local paths, and a public provider base URL. It is tracked intentionally. No `.env` or default database file was present during this audit. Git ignored representative `.env`, `.env.local`, nested `.env.production`, `*.db`, upload, and frontend build paths after the `.gitignore` fix. A publication follow-up added explicit `.coverage`, `coverage.xml`, `htmlcov/`, `frontend/coverage/`, `.mypy_cache/`, and `.ruff_cache/` exclusions to `.gitignore` and corresponding `.dockerignore` rules. `git check-ignore -v` confirmed representative paths. `.dockerignore` also excludes environment files, Git, databases, uploads, dependencies, and build output. Custom database/upload paths outside those patterns still require operator review.

No credential rotation or history rewrite is indicated by the observed scan. If a credential is later found in published history, rotate it and assess history cleanup before publication.

## 3. Hardcoding and configuration classification

| Classification | Evidence | Assessment |
| --- | --- | --- |
| Confirmed security defect — fixed locally | The original Docker image allowed `0.0.0.0` with auth off and hosts `*`. `Dockerfile`, `backend/app/config.py`, and startup tests now require container auth, a token of at least 32 characters, explicit hosts, and absolute storage paths. | Unsafe combinations fail before database initialization. The image itself could not be built or run because Docker Desktop's Linux engine is unavailable. |
| Deployment defect — fixed locally | `render.yaml` previously used `autoDeployTrigger: commit`; it now uses `off` and `*.onrender.com,localhost,127.0.0.1` trusted hosts. | Future Blueprint sync behavior was not observed; remote Render state was not changed. An explicit custom domain would need to be added. |
| Maintainability issue | `backend/app/config.py`, `backend/app/schemas.py`, `backend/app/automation.py`, `backend/app/scheduler.py`, and frontend components repeat `Asia/Calcutta`. | A configured timezone is not consistently the UI/planner default. Centralize exposed defaults after defining whether it is a user preference or process setting. |
| Maintainability issue | `frontend/package.json:1` uses `latest` for major build/runtime packages; `requirements.txt` uses broad ranges. | `npm ci` uses the lockfile today, but lockfile regeneration and Python installs may resolve different versions. Pin or constrain deliberately after compatibility review. |
| Acceptable default | Loopback hosts/ports in `run.ps1`, Vite proxy, and CORS; default SQLite/upload paths; public OpenAI and GitHub API endpoints. | These are local development or documented service defaults, not embedded credentials or machine-specific paths. No `D:\FlowForge` runtime path was found. |

The local launcher sets `FLOWFORGE_HOST`, which the backend configuration does not read; Uvicorn gets its host directly from the command arguments. This is a harmless dead setting.

## 4. Backend, API, database, and integration verification

**Passed:** Final full suite: **47 passed, 0 failed, 0 skipped, 1 warning** under pytest 9.1.1. The warning is a Starlette `httpx` TestClient deprecation. Tests use temporary SQLite databases, real application routes/services, and controlled fake provider responses. Verified paths include task lifecycle, graph/cycle validation, dependency order, output schemas, approvals, cancellation, retries and classification, run idempotency, startup recovery, schedule deduplication/time zones, document upload/download/type rejection, AI summary persistence, an approved automation that reads and writes real local records, container startup rejection, auth/host/CORS checks, and temporary SQLite backup/restore.

**Coverage gaps:** No general pagination API exists. Search has a bounded query and result limit but no pagination; response schema tests are selective. No explicit DB locking/contention or crash-between-external-success-and-commit test exists. No aggregate upload/storage quota, login rate limit, or malicious PDF stress test exists. The document regression test verifies that invalid text does not expose parser details in API-visible status.

## 5. Frontend and browser verification

**Passed:** Vitest/React Testing Library: **2 files, 8 tests passed**. The tests cover auth token entry, task actions, automation interaction paths, and refusal to submit invalid workflow output JSON while keeping the input editable. `npm run build` passes and includes `tsc -b` plus a Vite production bundle.

**Passed:** One Playwright smoke test ran in installed Microsoft Edge against the built frontend and real FastAPI application. It used a temporary SQLite database and a deterministic fake planner. It created a task, verified persistence after reload, confirmed execution was unavailable before approval, approved and executed a local plan, checked the saved artifact, used named controls at 390 px width, checked for horizontal overflow, and asserted no page or console errors. This is browser evidence for those paths only. CI is configured to install Chromium on Ubuntu, but that Linux CI path was not executed locally.

**Coverage gaps:** Current component/browser tests do not cover every loading/empty/failure state, document interaction, schedule editor, screen-reader behavior, or multiple device sizes. No live AI service was used.

**Fixed:** Settings now renders the numeric configured concurrency limit rather than a literal JSX expression (`frontend/src/App.tsx:91`).

## 6. Security assessment

**Verified controls:** Constant-time bearer token comparison when enabled; auth gate on non-exempt API routes; narrow development CORS origins; upload byte and type limits; UUID storage names; static-file containment check; parameterized SQL values and allowlisted dynamic update fields; no arbitrary HTTP/shell/Python tool in the registry; plan hash and saved-run tool permission checks; retry block after attempted writing effects. Document-derived instructions remain data and deadline candidates require review.

**Unresolved issues:**

| Severity | Evidence | Recommended action |
| --- | --- | --- |
| Medium for remote deployment | Container startup now fails on missing auth, short token, wildcard/empty hosts, and relative storage paths, but no image build or live HTTPS deployment ran here. | Build and test the actual image and verify host routing, TLS termination, persistent mounts, and token handling before deployment. |
| Medium | No login rate limit; one bearer token lives in browser `sessionStorage` (`backend/app/main.py:111`, `frontend/src/App.tsx:18`). | Add throttling and a rotation/session strategy before wider access. |
| Medium | Upload accepts a bounded compressed PDF but extraction has no page/text, CPU, or aggregate disk quota (`backend/app/main.py:319-337`). | Add processing and storage limits and exercise adversarial PDFs. |
| Medium | Arbitrary configured `FLOWFORGE_AI_BASE_URL` receives the AI credential; there is no URL allowlist or private-address policy (`backend/app/config.py:21`). | Validate endpoint policy if configuration can be influenced by less-trusted operators. No user-controlled general HTTP connector exists. |
| Medium | A crash after an external effect and before result commit cannot guarantee exactly-once execution (`backend/app/engine.py`). | Reconcile the effect before replay; add provider idempotency when a supported write integration is introduced. |
| Low | Render host list allows all `*.onrender.com` subdomains and local health probes; health is public; no total disk quota. | Replace with the assigned service hostname when known and set operational limits. The health response now exposes only `status` and `database`. |

**Fixed:** Document extraction failures previously stored parser exception strings in API-visible status (`backend/app/main.py:334,365`). Both upload and retry now store a generic reason. No exploit or actual secret disclosure was demonstrated.

Python static review found no `eval`, `exec`, shell invocation, pickle loading, or user-selected arbitrary HTTP tool in `backend/app`. SQL dynamic columns are derived from typed model fields or fixed migration definitions; values are parameterized. This review is not a substitute for Bandit.

## 7. Dependencies, static checks, and code quality

- Tool versions observed: Python 3.12.14 in `.venv`; pytest 9.1.1; pip-audit 2.10.1; Bandit 1.9.4; Node 24.18.0; npm 11.16.0; Vitest 5.0.4; Vite 8.3.4. CI specifies Python 3.12 and Node 22. A publication follow-up used Node 22.23.4 with npm 11.16.0 for a clean `npm ci`, then ran the frontend tests, lint, TypeScript check, and Vite build with that Node binary; all passed. The previous lockfile held TypeScript 7.0.2 under a `latest` manifest spec. The new `typescript-eslint` 8.72.0 declares a peer range of `>=4.8.4 <6.1.0`; npm resolved compatible TypeScript 6.0.3. Both manifest and lockfile now pin 6.0.3. Linux CI and its bundled npm version remain unverified until the actual workflow runs.
- `npm audit` against the updated lockfile passed: **0 vulnerabilities** across 259 reported dependencies.
- Initial `pip-audit --local` found **14 advisory records in 2 packages** (duplicate records for seven unique IDs): pip 25.0.1 and test-only pytest 8.4.2. The pytest advisory concerns local Unix temporary directories ([upstream pytest changelog](https://pytest.org/en/stable/changelog.html)); it is less directly applicable to this Windows run but relevant to Ubuntu CI. `requirements.txt` now requests pytest >=9.0.3,<10; local pytest 9.1.1 and pip 26.2.2 were tested. The Docker build now requests pip >=26.2,<27. Final `pip-audit --local` and `pip-audit -r requirements.txt` both passed with **no known vulnerabilities**. No other dependencies were upgraded for this finding.
- Bandit scanned 2,010 lines in `backend/app` and exited 1 with **3 reviewed findings**: B311 low at `engine.py:293` uses pseudo-random retry jitter, not a security token; B608 medium at `main.py:271,314` sees dynamic SQL column lists built from typed Pydantic patch models with bound values. No Bandit rule was suppressed. Neither finding demonstrates an exploitable injection path in the current code. A future change to those patch models should retain the column allowlist boundary.
- SQLite uses per-operation connections, foreign keys, rollback on exceptions, indexes, and a 10-second busy timeout. Outbound HTTP calls reuse an application-lifetime client in the API process. The scheduler is single-process and is cancelled on shutdown.
- The large `backend/app/main.py` and one-line JSX sections of `frontend/src/App.tsx` make security review and maintenance harder. No profiling claim is made. Graph validation is bounded to short automation plans; no performance optimization was justified by this audit.

## 8. CI, deployment, and hooks

GitHub Actions uses read-only `contents` permission, Python 3.12, Node 22, `npm ci`, backend tests, Python compilation, frontend tests, a real ESLint command, TypeScript/Vite build, and a Playwright Chromium smoke test. The lint script uses ESLint flat configuration with JavaScript, TypeScript, and React Hooks recommended rules; `--max-warnings 0` makes warnings fail. Local lint passed with no warnings. The workflow's Linux browser installation and run were not executed from this Windows workspace.

No Husky, lint-staged, pre-commit, configured `core.hooksPath`, or active repository hook was found. Recommended minimal hook: staged whitespace and a local staged secret scan in pre-commit, documented with manual equivalents. A pre-push hook can run the fast test/build checks, but CI remains the authoritative full suite. No hook was installed during this audit.

The Render blueprint has a persistent disk, auth token generation, single Docker web service, [manual deployment mode](https://render.com/docs/blueprint-spec), and `/api/health` check. The runtime command points to the actual FastAPI app with one Uvicorn worker. A temporary SQLite online backup/restore passed locally. A live Render build, disk mount, health endpoint, scheduler singleton, and HTTPS behavior were **not verified**. Running multiple worker processes would violate the documented scheduler design; the scheduler has occurrence deduplication but no cross-process lease. Remote state was untouched. Docker Desktop's Linux engine was unavailable, so the image could not be built or started locally.

## 9. README, banner, and documentation

The working README has a real relative banner link and ten local links; all ten resolve locally. The SVG parses as XML, has a 1200×220 viewBox, and contains the FlowForge wordmark plus PLAN/APPROVE/RUN/VERIFY text. The one Mermaid block has a flowchart declaration. Exact visual clipping and Mermaid rendering on GitHub were **not verified**. There is no fake demo URL; the CI badge URL targets the configured GitHub repository. The README distinguishes mocked provider tests from live service/deployment verification. Detailed setup and troubleshooting remain in the untracked `docs/OPERATIONS.md`. The README count was corrected to the final 47 backend, 8 frontend, and 1 browser result.

## 10. Command evidence

Commands below were run from `D:\FlowForge`; `0` means success, `1` failure/interruption. The first table is retained as evidence from the initial audit. Read-only source inspections also used `Get-Content`, `rg`, `git diff`, and `git ls-files` with no repository mutation.

| Exact command | Exit / result |
| --- | --- |
| `git status --short --branch`; `git remote -v`; `git ls-files`; `git diff --cached --stat`; `git diff --stat`; `git log -5 --oneline` | 0 each; no staged files |
| `git diff -- README.md docs/ARCHITECTURE.md docs/ENGINEERING_AUDIT.md docs/USAGE.md docs/assets/flowforge-banner.svg` | 0; all pre-existing edits inspected |
| `.venv\Scripts\python.exe .tmp/audit_scan.py` | 0; 59 tracked paths, 79 unique history blobs, no matches |
| `git check-ignore -v .env .env.local backend/.env.production data/flowforge.db storage/documents/test.pdf frontend/dist/index.html` | 0; all representative paths ignored |
| `.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp .pytest-tmp` | Interrupted (1); sandbox Windows asyncio socket-pair stall |
| `.venv\Scripts\python.exe -m pytest backend/tests/test_api.py::test_persisted_task_and_workflow_lifecycle -vv -s -o faulthandler_timeout=15 --basetemp .pytest-run/diagnostic` | Interrupted (1); trace located sandbox socket-pair stall |
| `.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp .pytest-run/audit-full` | 0 outside sandbox; 37 passed before fixes |
| `.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp .pytest-run/audit-final` | 0 outside sandbox; **38 passed**, 1 deprecation warning after fixes |
| `npm --prefix frontend test` | 0; 7 passed, rerun after fixes |
| `npm --prefix frontend run build` | 0; `tsc -b` and Vite build, rerun after fixes |
| `.venv\Scripts\python.exe -m compileall -q backend/app` | 0; rerun after fixes |
| `npm --prefix frontend run --if-present lint` | 0; **no lint command executed** |
| `npm --prefix frontend audit --audit-level=low --json` | First run 1 (sandbox DNS); approved retry 0, zero vulnerabilities |
| `.venv\Scripts\python.exe .tmp/check_docs.py` | 0; links and SVG XML pass; Mermaid declaration checked |
| `git diff --check` | 0; CRLF conversion warnings only |
| `git rev-list --left-right --count HEAD...origin/main` | 0; local tracking result `0 0` |
| `git config --get core.hooksPath` | 1; no configured path |

The continuation used these exact verification commands after the code changes:

| Exact command | Exit / result |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest backend/tests/test_http_integration.py -q --basetemp .pytest-run/container-security` | 0; 13 passed under pytest 8 before the targeted advisory update |
| `.venv\Scripts\python.exe -m pytest backend/tests/test_api.py backend/tests/test_http_integration.py -q --basetemp .pytest-run/security-and-backup` | 0; 20 passed under pytest 9 after startup and backup changes |
| `.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp .pytest-run/pre-release-final` | 0; **47 passed**, 1 upstream deprecation warning |
| `npm --prefix frontend run lint` | Initial run 1 with 6 errors and 2 warnings; final run 0 with no warnings |
| `npm --prefix frontend test` | Initial post-Playwright run 1 because Vitest collected the E2E spec; final run 0, **2 files / 8 tests passed** after exclusion |
| `npm --prefix frontend run build` | One intermediate run 1 for an invalid Testing Library `exact` option in the new test; final run 0 after correcting the selector, with TypeScript `tsc -b` and Vite production build passed |
| `npm --prefix frontend run test:e2e` | Sandbox attempt interrupted (1) due local socket/browser startup; approved final run 0, **1 Playwright Edge test passed** |
| `.venv\Scripts\python.exe -m compileall -q backend/app` | 0 |
| `.venv\Scripts\python.exe -m pip_audit --local -f json -o .tmp/pip-audit.json` | Sandbox DNS failure 1; approved initial run 1 with 14 advisory records in pip/pytest |
| `.venv\Scripts\python.exe -m pip_audit --local -f json -o .tmp/pip-audit-final.json` | 0 after targeted updates; no known vulnerabilities |
| `.venv\Scripts\python.exe -m pip_audit -r requirements.txt -f json -o .tmp/pip-audit-requirements.json` | 0; no known vulnerabilities in resolved requirements |
| `.venv\Scripts\python.exe -m bandit -r backend/app -f json -o .tmp/bandit-final.json` | 1; 3 findings individually reviewed in section 7 |
| `npm --prefix frontend audit --audit-level=low --json` | 0; zero vulnerabilities after added dev dependencies |
| `.venv\Scripts\python.exe .tmp/audit_scan.py` | 0; 59 tracked, 6 untracked paths, 79 history blobs, no matches |
| `docker info --format '{{.ServerVersion}}'` | 1; Docker Desktop Linux engine unavailable, so no image build/run |
| `git status --short --branch`; `git diff --cached --name-only`; `git diff --check` | 0 each; all changes remain unstaged and no whitespace errors (Git emitted CRLF notices) |
| `git check-ignore -v .env .env.local backend/.env.production data/flowforge.db storage/documents/test.pdf frontend/dist/index.html frontend/test-results/example frontend/playwright-report/example` | 0; all representative sensitive/generated paths ignored |
| `.venv\Scripts\python.exe .tmp/check_docs.py` | 0; ten README local links resolve, SVG XML parses, Mermaid declaration checked |
| `.venv\Scripts\python.exe -c "import yaml; x=yaml.safe_load(open('render.yaml')); s=x['services'][0]; print(s['autoDeployTrigger'], s['disk']['mountPath'], [v['value'] for v in s['envVars'] if v['key']=='FLOWFORGE_ALLOWED_HOSTS'][0])"` | 0; output confirms `off`, `/var/data`, and explicit host pattern |

The tool installations were local only: `npm --prefix frontend install --save-dev eslint @eslint/js typescript-eslint eslint-plugin-react-hooks globals`, `npm --prefix frontend install --save-dev @playwright/test`, `.venv\Scripts\python.exe -m pip install pip-audit bandit`, and `.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2,<27" "pytest>=9.0.3,<10"` all exited 0 after network approval. The initial sandboxed npm and pip install attempts failed because registry access was unavailable. No tool output or test database was added to Git.

## 11. Recommended publishing checklist — do not execute as part of this audit

1. Review every local edit, including this untracked report and the pre-existing documentation changes; decide which files should eventually be published.
2. Rerun lint, backend/frontend/browser suites, TypeScript/build, Python compilation, dependency audits, and `git diff --check` after any further source or dependency changes.
3. Review Bandit's three informational findings if the retry or SQL patch boundaries change; retain the typed-column and bound-value protections.
4. Build and run the actual Docker image with safe and unsafe environment combinations; verify startup refusal, trusted hosts, auth, TLS termination, health, and persistent mounts. The Docker Linux engine was unavailable here.
5. Confirm whether Render is connected and that manual deployment remains configured. Test a real disk-backed restart, backup retrieval, restore drill, and health check before claiming deployment readiness.
6. Run the CI browser job on its configured Linux/Chromium runner before treating the local Edge result as cross-platform evidence.
7. Repeat a current Git secret scan, inspect `git status` and staged diff, and review any custom storage paths immediately before a future publication decision.

This report records local evidence only. Nothing in this checklist was committed, pushed, deployed, or applied to remote GitHub state.
