# ADR 0004: No general application cache yet

Status: accepted (2026-10-20)

## Decision

Do not cache workflow state, schedules, approvals, task records or notifications. No Redis or process cache is introduced in this phase. Re-evaluate repeated work using the benchmark and request profile before adding a cache.

The initial 100-sample local baseline found empty-due scheduler polling at p95 1.72 ms and did not measure repeated read traffic or a cache hit/miss comparison. This is insufficient evidence that cache complexity improves the current workload. The larger observed cost is the 40-node SQLite execution path (p95 629.7 ms); optimize transaction/query behavior and profile real repeated requests before considering a cache.

## Candidate policy

Any future cache needs a documented key, invalidation, size bound, TTL, sensitive-data policy and failure behavior. Deterministic document extraction could be keyed by content hash plus extractor/version, with bounded retention and no cross-user sharing. AI result reuse would need content hash, exact model/version, prompt version, parameters and explicit freshness/retention behavior; outputs may contain sensitive content. Cache failure must fall back to computing the result. Side effects and authoritative workflow state must never depend on cached values.
