<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Write an analyzer

An **analyzer** reads a results tree and writes a report. It never runs a
run, so any machine that has the merged tree can run it: the machine that
executed the runs is not needed.

```yaml
experiment:
  analyzer:
    plugin: native
    params: {}
```

`native` writes `analysis/summary.json` (runs planned, complete, and which are
not, per scenario). It is what `mas-lab benchmark runs summary` runs, and what
CI uses to decide whether another retry round is needed. Statistics, plots and
judges stay in the experiment's `run.post` / `experiment.post` pipeline, which
`mas-lab benchmark pipeline run` can also run over a merged tree.

## Interface

```python
from mas.lab.benchmark.execution.roles import AnalysisContext, reject_unknown_keys

class Mean:
    plugin_id = "mean"

    def validate_params(self, params: dict) -> None:
        reject_unknown_keys(params or {}, set(), plugin_id=self.plugin_id)

    def analyze(self, ctx: AnalysisContext) -> dict:
        # ctx.results_root, ctx.runs, ctx.dir_names, ctx.params
        return {"planned": len(ctx.runs), "incomplete": []}
```

Return a mapping with `incomplete` (run ids) when the report should gate
`runs summary --fail-on-incomplete`.

## Register it

```yaml
types:
  - analyzer
plugins:
  - type: analyzer
    name: mean
    module: my_lab_lib.analyzers
    class: Mean
```
