# Local workflow benchmark

Run from the `backend` directory so Python can resolve the application package:

```powershell
python -m benchmarks.workflow_benchmark --iterations 100 --warmup 10 --node-concurrency 4 --concurrent-runs 4 --provider-delay-ms 20
```

The committed [`local-baseline.json`](local-baseline.json) was measured on 2026-10-20 using Windows 11, Python 3.12.14, SQLite 3.53.1, 100 measured samples plus 10 warmups per workload. The provider was a deterministic in-process stub that slept 20 ms and failed once per execution; it made no network request. All measured workloads completed without failures.

| Workload | p50 | p95 | Runs/sec |
| --- | ---: | ---: | ---: |
| 5-node dependent workflow | 74.1 ms | 120.7 ms | 11.216 |
| 40-node dependency graph | 472.2 ms | 629.7 ms | 1.982 |
| 64 KB synthetic TXT extraction plus workflow serialization | 52.0 ms | 64.8 ms | 17.667 |
| Simulated slow/transient provider | 104.2 ms | 129.3 ms | 8.691 |
| Four concurrent 10-node executions | 438.2 ms | 578.0 ms | 8.020 |

The 100 empty-due scheduler polls measured p50 1.11 ms and p95 1.72 ms. SQLite reported indexed searches using `idx_tasks_status_reminder_due` for due tasks and `idx_schedules_enabled_next` for schedules. Because timestamps are compared through `julianday()` to handle offsets safely, SQLite uses the equality prefix (`status` or `enabled`) rather than a lexical timestamp range. The benchmark database ended at 34,680,832 bytes after synthetic runs and document records. This highlights the need for a future retention policy if run histories grow; no data-retention behavior is introduced here. CPU time is process-wide, and Windows peak RSS is unavailable in this runtime.

## Interpretation and limits

The baseline shows that a 40-node graph costs materially more than a 5-node graph in this SQLite-per-transition implementation. Four concurrent runs completed with zero errors, but this is a single-process local benchmark and does not establish multi-process write capacity. Per-node operation latency rounds to zero milliseconds for many transform nodes; end-to-end workflow latency is the useful comparison. The provider result includes the configured 20 ms artificial delay and local retries, not internet or provider latency.

There is no before/after cache comparison because the application has no cache. The benchmark confirms no reason to cache authoritative state: scheduler due polling is already low in this synthetic empty-due case, while run and document results require freshness and privacy semantics. Repeated API polling and large histories remain candidates for profiling with real usage. AI result caching is deferred until keying, model/version identity, invalidation, sensitive-content retention, and cost benefit are measured. The benchmark does not measure HTTP connection reuse; pooling was implemented as lifecycle-based efficiency work, and live provider/GitHub requests were not made.

These numbers are a reproducible local baseline, not a capacity claim or hosted-service SLO. Hardware, filesystem, Python/SQLite versions, and workload data affect results. Re-run the script on the target machine before using them for sizing decisions.
