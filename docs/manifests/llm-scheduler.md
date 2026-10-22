<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM scheduler

Pace live LLM calls with the **`llm_scheduler` infra middleware** — the same
pipeline slot as `llm_cache`, stacked **inside** the cache so replay never
waits on a rate limit.

Attach `kind: InfraMiddleware` (`standard:llm-scheduler`) or the bundle
`standard:llm-proxy-paced` (cache → scheduler → OpenAI). First merged pipeline
entry is outermost: list cache before the scheduler.

**Prerequisites:** Tutorial 0 (`task install`, `.venv` on `PATH`).

---

## Why this sits after routing and after cache

The infra pipeline wraps the provider. There is no pre-routing hook in this
model: a limiter can only see the engine that routing already selected.

Do not pace cache lookups. A hit is local I/O; only a miss should acquire a
live slot:

```text
pipeline: [ llm_cache, llm_scheduler, <provider> ]

invoke: llm_cache → llm_scheduler → LiveLlmEngine → provider
```

Cache hits return before `llm_scheduler.invoke`. Misses (runtime `LLM_CALL`
and pipeline / LLMaaJ live completions) share one file-backed store so
multithreaded and multiprocess workers cannot DDoS the provider.

---

## What is limited

| Knob | Default | Meaning |
| --- | --- | --- |
| `max_in_flight` | `8` | Concurrent live requests per bucket. `0` = no in-flight cap. |
| `requests_per_minute` | `0` | Token-bucket RPM. `0` = concurrency-only. |
| `keys` | `[provider, model]` | Bucket dimensions. |
| `user` | `default` | Label when `keys` includes `user`. |
| `timeout_s` | `300` | Wait for a slot before `TimeoutError`. |
| `state_path` | `$XDG_CACHE_HOME/mas/llm_scheduler.json` | Shared JSON + sibling `.lock`. Unix uses `flock`; Windows is process-local. Crashed holders drop when the pid dies. Waiters raise `TimeoutError` after `timeout_s`. |

`keys` chooses the policy grain:

| `keys` | Effect |
| --- | --- |
| `[provider, model]` (default) | One ceiling for that model on that host, shared by every runtime, pipeline, and experiment on this machine. |
| `[provider]` | One ceiling for the whole provider (all models). |
| `[provider, model, user]` | Fairness across callers. Set `MAS_LLM_SCHEDULER_USER` (experiments set this to the experiment name). |

Per-run isolation is the wrong default: eight parallel runs of the same
experiment would each think they own eight in-flight slots. Leave `user` out
of `keys` for a global cap; add it when two evaluations must share the GPU
fairly.

---

## Runtime and pipelines share the limiter

`LlmSchedulerMiddleware.wrap` installs a process-global policy.
`install_openai_llm_service` copies that policy from the resolved infra
pipeline. Judge / `complete_json` live HTTP (after the judge cache miss)
takes a slot with `llm_slot` — the same buckets as agent `LLM_CALL`.

Cache hits on either path never acquire a slot.

---

## Sample refs

| Manifest | Ref | Purpose |
| --- | --- | --- |
| Scheduler | `standard:llm-scheduler` | In-flight 8, no RPM, keys `[provider, model]` |
| Provider + cache + pace | `standard:llm-proxy-paced` | `InfraBundle`: cache, then scheduler, then OpenAI |

```bash
mas-ctl chat agent.yaml --infra-ref standard:llm-proxy-paced
```

Or stack refs (cache first):

```bash
mas-ctl chat agent.yaml \
  --infra-ref standard:llm-cache \
  --infra-ref standard:llm-scheduler \
  --infra-ref standard:openai
```

Env overrides (last resort, same as other `MAS_LLM_*` knobs):

| Variable | Overrides |
| --- | --- |
| `MAS_LLM_MAX_IN_FLIGHT` | `max_in_flight` |
| `MAS_LLM_RPM` | `requests_per_minute` |
| `MAS_LLM_SCHEDULER_USER` | `user` (lab execute sets this to the experiment name when unset) |
| `MAS_LLM_SCHEDULER_PROVIDER` | `provider` |
| `MAS_LLM_SCHEDULER_STATE` | `state_path` |

---

## See also

- [LLM cache](llm-cache.md) — stack cache outside this middleware
- [Infra manifests](infra.md)
- Plugin: `mas.library.standard.plugins.infra.llm_scheduler.LlmSchedulerMiddleware`
- Params schema: [`infra-llm-scheduler-params.schema.yaml`](../schemas/runtime/fragments/infra-llm-scheduler-params.schema.yaml)
- Store: `mas.runtime.engine.llm_scheduler`
