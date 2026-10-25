# ADR 006: Approved, registered tools for personal automations

Date: 2026-10-24

Status: Accepted for the current local application

## Context

FlowForge already has a DAG executor, a scheduler, SQLite records, and an optional AI provider. A model-generated plan must never acquire arbitrary code or network capabilities, and a successful model response is not evidence that a user action occurred.

## Decision

Use one strict planner response to create a proposal of at most 12 steps. Validate tool IDs, versions, typed arguments, dependencies, and static record targets on the server. Store proposals separately from executable workflows. Bind user approval to the exact definition and schedule hash. Only a saved definition carrying a valid approval reference can execute registered tools. At dispatch, compare the requested node with the run snapshot. Keep the existing executor, scheduler, and API authentication boundary.

Each tool declares its permission, side effect, limits, retry policy, and output schema. Local writes use read-back verification and run/node idempotency where possible. AI-extracted document dates remain candidates. Whole-run retry is disabled when any write or external call was attempted, because a prior result may be uncertain after a crash.

## Consequences

The normal path has an explicit review step and stable, inspectable history. Editing a saved automation requires proposing and approving a new version. This favors correctness over in-place convenience. There is no unrestricted HTTP or nested execution tool. Adding external APIs requires a connector with DNS/redirect SSRF protection, credential references, response validation, reconciliation, and dedicated tests. The existing process-local scheduler and SQLite remain availability limits.
