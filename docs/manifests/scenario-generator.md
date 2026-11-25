<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Write a scenario generator

A **scenario generator** answers *what runs*: which runs (scenario × item × replicate) exist.
The lab ships `cartesian`, `coupled`, `one_factor`, and `factorial` in `library-lab`.
A lab-local library can add another and name it from the experiment spec.

Experiment YAML:

```yaml
experiment:
  design:
    plugin: cartesian          # default when omitted
    max_executions: 200        # core guard, every generator
    params: {}                 # plugin attributes
```

`run.n_runs` stays under `run`. Item-level `scenarios:` filters run after the generator.
Unknown plugin ids fail at config load with the registered list. Params are validated then too.

## Interface

```python
from mas.lab.benchmark.execution.roles import PlannedRun, DesignContext

class MyGenerator:
    plugin_id = "my_generator"
    default_scheduler = "coverage"   # used when schedule.plugin is omitted

    def validate_params(self, params: dict) -> None:
        if "keep" not in params:
            raise ValueError("params.keep is required")

    def generate(self, ctx: DesignContext) -> list[PlannedRun]:
        keep = set(ctx.params["keep"])
        return [
            PlannedRun(scenario_id, item, run_idx)
            for run_idx in range(ctx.n_runs)
            for scenario_id in ctx.scenario_ids
            if scenario_id in keep
            for item in ctx.dataset_items
        ]
```

`DesignContext` carries `scenario_ids`, `dataset_items`, `n_runs`, and `params`.
The plugin never reads the experiment file. Same input, same runs.

## Register it

In the lab library's `library.yaml`:

```yaml
types:
  - scenario_generator
plugins:
  - type: scenario_generator
    name: my_generator
    module: my_lab_lib.generators
    class: MyGenerator
```

List that library under `lab.libraries` in `lab-config.yaml`. Then:

```yaml
experiment:
  design:
    plugin: my_generator
    params:
      keep: [baseline, treatment]
```

See [experiment.md](experiment.md#design) for the shipped plugins and [lab.md](lab.md) for lab-local libraries.
