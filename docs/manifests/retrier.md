<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Write a retrier

A **retrier** answers *does this run run again, and after how long?* It is
separate from the [executor](executor.md) (which runs a run once) so the same
decision serves two places: between attempts inside one process, and between
**rounds**, for instance a CI job that was re-run or a second workflow dispatch.

```yaml
experiment:
  retry:
    plugin: native
    params:
      max_attempts: 3   # total tries, the first included; 1 disables retry
      backoff_s: 2.0    # base delay, doubled each time; Retry-After lengthens it
```

`native` does not retry an auth, TLS or malformed-spec error, or a result an
executor marked `fatal`. `max_attempts` counts attempts across rounds: a run a
CI round already tried twice has one try left under `max_attempts: 3`.

## Interface

```python
from mas.lab.benchmark.execution.roles import AttemptRecord, RetryDecision

class Twice:
    plugin_id = "twice"

    def validate_params(self, params: dict) -> None: ...

    def decide(self, history: list[AttemptRecord]) -> RetryDecision:
        return RetryDecision(retry=len(history) < 2, delay_s=0.0, reason="two tries")
```

`decide` is pure: the same history gives the same decision, and it reads no
file, clock or network. The history has one record per attempt so far
(`status`, `error`, `fatal`).

In-process, `run_with_retrier` joins executor and retrier. Across rounds,
`mas-lab benchmark runs retry` rebuilds a history from the `attempts` and
`error` in each run's `result.json` and asks the same `decide`.

## Register it

```yaml
types:
  - retrier
plugins:
  - type: retrier
    name: twice
    module: my_lab_lib.retriers
    class: Twice
```
