<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Execution tuning (not an Agent field)

This page tells you where engine tuning goes.
`spec.execution` on `kind: Agent` and `spec.patch.execution` in an overlay are not supported.
Put engine queue depth, cache policy, streaming, and parallel tool dispatch in a `kind: RuntimeEngine` manifest.

The runtime resolves a `RuntimeEngine` the same way as infra. It looks in workspace `runtime_refs`, then in `$XDG_CONFIG_HOME/mas/runtime/`, or takes the CLI flag `--runtime-ref`.

Smallest replacement for an old `spec.execution` block:

```yaml
apiVersion: infra/v1
kind: RuntimeEngine
metadata:
  name: my-runtime
spec:
  engine:
    queue_depth: 64
  stream: false
```

See [runtime-engine.md](runtime-engine.md) and [user-config.md](../user-config.md).

## Migration table

| Old (invalid on `kind: Agent` / overlay patch) | Replacement |
| --- | --- |
| `spec.execution` (cache, stream, queue depth, …) | `kind: RuntimeEngine` via `runtime_refs` / `--runtime-ref` |
| Offline LLM turns | [llm_cache replay](llm-cache.md) recorded against a live provider (`raise_on_miss`) |
| `spec.infra_refs` / `spec.runtime_refs` on Agent, MAS, or overlay | `config.yaml`, `--infra-ref` / `--runtime-ref` |
| `spec.infra_interceptors` on Agent | workspace / CLI infra resolution (same as infra refs) |

## Do not confuse with experiment settings

Batch orchestration for `mas-lab benchmark run` lives on the experiment manifest, not on agents.
The deprecated `experiment.execution` block is split into three keys:

- `experiment.design`: what the experiment compares.
- `experiment.schedule`: parallelism, timeouts, ordering, and retries.
- `experiment.bench_emulation`: the trace-cache posture (how runs reuse stored traces).

None of them replaces `RuntimeEngine`. See [Design vs schedule](experiment.md#design-vs-schedule).

`mas-ctl validate` and the separation checks reject the old keys, so outdated examples fail at once.

## See also

- [runtime-engine.md](runtime-engine.md)
- [experiment.md](experiment.md#design-vs-schedule)
- [llm-cache.md](llm-cache.md)
