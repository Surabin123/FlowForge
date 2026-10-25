# ADR 0005: Bounded in-process engineering telemetry

Status: accepted (2026-10-20)

## Decision

Use structured logs and a small bounded in-process metrics registry. Metrics are diagnostic, reset on process restart, and are not a durable source of workflow state. Do not add an exporter or user-facing dashboard in this phase.

The API reuses one `httpx.AsyncClient` with bounded connection/keepalive limits for AI and GitHub requests. A wrapper measures outbound call duration and status group without recording the URL, headers, prompt, payload, or provider response. A standalone short-lived client is used only when the helper is invoked outside application lifespan, such as a direct test call.

## Trade-offs

The registry stores only a fixed set of metric names and bounded recent duration samples. Counters and duration summaries can be inspected in tests or a local engineering-only diagnostic path. Logs carry correlation IDs and safe error categories, not tokens, prompts, document contents, request bodies or provider response bodies. Provider usage tokens may be counted when the response contains valid usage values; cost is omitted without a trustworthy model pricing table.

If multi-process workers or an external operations team become requirements, move aggregation to a supported metrics/logging service only after defining access control and retention.
