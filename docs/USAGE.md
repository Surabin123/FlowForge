# Using FlowForge

FlowForge keeps personal records locally and runs automations only through the capabilities shown in the app. This guide describes what each area actually does.

## Start with your workspace

1. Launch the API and UI using the [quick start](../README.md#quick-start) or the [platform-specific operations guide](OPERATIONS.md).
2. Add tasks with titles, priorities, and optional due dates. Completion and edits update the SQLite record.
3. Add study subjects and sessions. Completing a session updates its stored status and the study totals.
4. Upload a PDF, TXT, Markdown, or CSV document. FlowForge extracts searchable text and keeps the original file in local storage. The upload limit is set by `FLOWFORGE_MAX_UPLOAD_MB`.

Task due dates and reminders are checked while the API process is running. Due alerts are written to the in-app notification history. Daily, weekday, and weekly recurrence use the configured IANA timezone. They are not OS, email, or mobile push notifications.

## Create a personal automation

The **Automations** area accepts a plain-language goal. Planning needs `FLOWFORGE_AI_API_KEY` and `FLOWFORGE_AI_MODEL` in the ignored `.env` file. The model proposes steps; it cannot execute them directly.

1. **Describe the goal.** Example: “Every weekday at 9 AM, read my open tasks, prioritize them, and save a daily plan.” Include a date, time, and timezone when a schedule matters.
2. **Inspect the proposal.** The page shows every tool, its parameters, dependencies, and whether it reads local data, writes local data, or calls the AI provider. If a key detail is missing, the proposal asks for clarification instead of creating runnable steps.
3. **Edit if necessary.** Change the title, summary, step parameters, dependencies, or schedule. The server revalidates the entire proposal. Any edit clears the previous approval.
4. **Approve exact steps.** Approval is tied to a hash of the current plan and schedule. A different title, tool, parameter, dependency, or schedule needs a fresh approval.
5. **Run or save.** **Run now** creates a persistent workflow run. **Save** stores the approved definition; if it has a time trigger, the existing scheduler records its next occurrence. The saved workflow can also be run manually.
6. **Inspect the outcome.** Each step records status, attempts, and a result. Local writes are checked after storage. Generated artifacts can be opened from the Automations page; run history keeps the execution record.

### Example: real daily plan

For an approved plan containing `tasks.list_open` → `plans.daily` → `artifacts.save`, FlowForge reads open tasks from SQLite, sorts their actual priorities and due dates, writes a generated artifact, and confirms it can read the artifact back. The task IDs in the artifact point to stored tasks. The integration test covers this path with a fake planner response and a real temporary database.

### Example: document dates

After you upload a document, an approved `documents.deadlines` step can ask the configured AI provider for structured deadline candidates. The document body is treated as untrusted data. The result is labeled **candidate output** because a model-produced date is not independently confirmed. FlowForge does not silently create tasks from those candidates; inspect the document and create or approve any task separately.

## Workflows and schedules

The **Workflows** area contains saved dependency graphs. Nodes run after their parents succeed. A manual gate pauses the run for a person to approve or reject that step. Existing non-tool workflows can use transform and configured AI agent nodes. Tool steps are only admitted through the reviewed automation-plan path.

The scheduler checks persisted one-time or recurring schedules in the API process. A unique occurrence claim prevents the same due time from creating two runs in normal single-process operation. An API restart marks interrupted running work as failed, preserves its history, and picks up queued scheduled runs. A run with an attempted local write or external request is blocked from blind whole-run retry; inspect what happened before starting new work. There is no resume-from-last-completed-node guarantee for such effects.

Schedules can be paused or resumed using the workflow schedule controls. Editing an approved saved automation in place is intentionally disallowed, so old run history continues to reference the exact definition that ran. Propose and approve a new version for future executions.

## Optional AI and GitHub connections

- **AI:** Configure the compatible endpoint, key, and model in `.env`. Planning and document analysis send the relevant request or selected document text to that provider. Missing credentials produce an explicit error; there is no fabricated result.
- **GitHub activity:** Configure an owner and a token with suitable read access to show repository, pull request, commit, and Actions activity in the Developer area. This connection does not push commits or modify GitHub.

The app has no general external HTTP automation tool, email/calendar integration, browser controller, or GitHub write action. Those require separate connectors, permission policies, validation, and tests.

## Data and hosting

The [operations guide](OPERATIONS.md) covers local data paths, configuration, backups, and remote exposure requirements. The [Render guide](DEPLOYMENT_RENDER.md) covers optional hosting; the included files are preparation, not evidence of a live deployment.
