<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Complete runs and stage modes

A **run** is one planned execution: a scenario, a dataset item, and a
repeat index. The **executor** decides whether that run is **complete**.
The host marks an incomplete run `error` and may schedule it again.

## Completeness

A run is complete only when the invoke finished as intended. These
outcomes are incomplete:

- an error status
- a timeout
- a lost worker
- a failure inside the run that the run's own output hides (`LLM request failed:`, or a `[send] agent …` failure including `via <protocol> failed` and `not available on bus`). A send that failed on an `llm_cache miss` is not that: the peer never ran, and the caller may continue.

`result.json` records `status: ok` only for a complete run. A resumed
batch re-runs runs left in `error`. Local leftover `events.jsonl` without
an `ok` result is not a cache hit.

Authorization, TLS, and a malformed spec are not retried. Other failures
retry up to `retry.params.max_attempts`, with exponential backoff that also
honours a `Retry-After` hint in the error text.

The executor's `is_complete` hook must never report an incomplete run as
complete. A batch pipeline stage must never start on one.

## Stage modes

Each pipeline stage declares how it reads runs:

| Mode | What it receives | Re-run |
| --- | --- | --- |
| `batch` | complete runs only | yes, bounded by the retry policy |
| `live` | events as they arrive, flagged live and possibly incomplete | no |
| `mixed` | live fold, then a completeness gate before any batch persist | gate only |

YAML:

```yaml
run:
  post:
    - type: extract_trace_stats
      mode: live
    - type: eval_mce
      mode: batch
```

Omit `mode` and it is inferred: a folding class (`on_batch`) or
`realtime: true` is `live`; execute-only or `realtime: false` is `batch`.

## Completeness gate

A **live** stage may feed a **batch** stage only through the completeness
gate: the run must be complete. A real-time telemetry pipeline cannot
wait for or re-run anything, so its live stages receive whatever arrives.

```text
extract_trace_stats (live) --complete--> eval_mce (batch)
extract_trace_stats (live) --any--> live dashboard
```

See [pipeline assembly](pipeline-assembly.md) for the typed catalog a UI
uses to draw that graph.

## Clean runs

A call that was refused and retried adds latency the experiment did not
intend to measure. Use the retry layer, not an executor parameter:
`retries: 0` turns a 429 into a classified failure of that call. The run
then ends incomplete, which the executor already handles.

`llm_call_end` records `retry_count` and `error_class` when the engine set
them. The `llm_scheduler` layer lowers its in-flight cap on a refusal and
raises it while calls stay clean, and pauses the bucket after a streak.
That is what a gateway sees. Pacing stays opt-in infra.
