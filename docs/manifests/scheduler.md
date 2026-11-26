<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Write a scheduler

A **scheduler** answers *in what order* the planned runs run.
The lab ships `coverage`, `depth`, and `as_generated` in `library-lab`.
A lab-local library can add another and name it from the experiment spec.

Concurrency (`parallel_scenarios`) and pacing (`pause_between_runs`) stay on
`schedule` as execute-loop settings; they are not scheduler params.
Pacing of one model call is the gateway's job (`Retry-After`) and, when
attached, the `llm_scheduler` infra layer.

```yaml
experiment:
  schedule:
    plugin: coverage          # omit to use the generator's default_scheduler
    params: {}
```

Unknown plugin ids fail at config load with the registered list. Params are
validated then too. Same input, same order: no clock, no unseeded randomness.

## Interface

```python
from mas.lab.benchmark.execution.roles import PlannedRun, ScheduleContext

class LongestFirst:
    plugin_id = "longest_first"

    def validate_params(self, params: dict) -> None:
        extra = sorted(set(params or {}))
        if extra:
            raise ValueError(f"params.{extra[0]} is not a longest_first key")

    def order(self, runs: list[PlannedRun], ctx: ScheduleContext) -> list[PlannedRun]:
        return sorted(runs, key=lambda run: (-len(run.scenario_id), run.run_idx))
```

`ScheduleContext` carries `scenario_ids`, `params`, and `parallel_scenarios`.
The plugin never reads the experiment file.

## Register it

```yaml
types:
  - scheduler
plugins:
  - type: scheduler
    name: longest_first
    module: my_lab_lib.schedulers
    class: LongestFirst
```

See [experiment.md](experiment.md#schedule) for the shipped plugins.
