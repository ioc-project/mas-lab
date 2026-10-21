<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Custom pipeline steps

This page shows how to write a custom pipeline step in Python. Read it when no built-in step does what you need.

A **custom pipeline step** is a `PipelineStep` subclass. It lives next to the experiment (this page uses a `lib/steps/` folder) or in a lab-local library. It behaves like a built-in step. It can be `per_run: true`, produce artifacts, depend on other steps, and run in `run.post` or experiment-level `post:` hooks.

Smallest working example: the [minimal step](#minimal-step) below, referenced as `type: lib/steps/my_eval.py:MyEvalStep`.

There are two ways to ship a step:

- **File reference** (this page): point `type:` at `path/to/file.py:ClassName`. The path resolves from the experiment directory. Nothing to register.
- **Lab-local library** (recommended for steps you reuse): put the class in a package listed under `lab.libraries`. Reference it as `package.module:ClassName` or by registered name. See [labs-going-further.md](labs-going-further.md).

Step types already shipped: [pipeline-steps.md](pipeline-steps.md).

---

## When to write a custom step

Write one when:

- You need **deterministic fixture evaluation**, for example comparing `run_action` tool calls to ground-truth labels, without an LLM judge.
- You need a **domain-specific plot** that the standard `plotnine` config cannot express.
- You need to call an external service or library that has no built-in step.

Otherwise use built-in steps: `eval_mce`, `collect_metrics`, `plotnine`, `extract_trace_stats`.

---

## Directory layout

```
my-experiment/
├── experiment.yaml
├── lib/
│   ├── __init__.py              # empty — makes lib/ a Python package
│   └── steps/
│       ├── __init__.py          # empty
│       └── my_custom_step.py    # one or more PipelineStep subclasses
├── register_steps.py            # optional — registers types for standalone CLI use
├── pipelines/
│   ├── post-run.yaml            # per-run hooks
│   └── post-experiment.yaml    # experiment-level hooks
└── ...
```

The `lib/` directory sits next to `experiment.yaml`. Its parent is `experiment_yaml.parent`. `build_runtime_pipeline` uses that directory as `base_dir` to resolve file-path step types.

---

## Minimal step

```python
# lib/steps/my_eval.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mas.lab.benchmark.pipeline import PipelineStep, StepOutput
from mas.lab.benchmark.pipeline.run_artifacts import resolve_run_events, run_dir_from_ctx

logger = logging.getLogger(__name__)


class MyEvalStep(PipelineStep):
    """Evaluate a single run against ground-truth labels.

    Declare with ``per_run: true`` in the pipeline YAML.
    """

    type = "my_eval"        # registered name (used in YAML)

    async def execute(self, ctx: Any) -> StepOutput:
        # 1. Get the run directory from execution context ───────────────
        run_dir = run_dir_from_ctx(ctx, self.config)
        if run_dir is None:
            raise ValueError(
                f"Step '{self.name}': run scope unavailable. "
                "Declare with 'per_run: true'."
            )

        # 2. Resolve events.jsonl (handles .run_ref and inline traces) ──
        events_path = resolve_run_events(ctx, self.config)
        if events_path is None or not events_path.exists():
            logger.warning("Step '%s': no trace for %s — skipping", self.name, run_dir)
            return StepOutput(data={"skipped": True}, files=[], metadata={})

        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        # 3. Evaluate
        score = _my_score(events)

        # 4. Write metrics.json (MCE format — collect_metrics reads this) ──
        metrics_path = run_dir / "metrics.json"
        metrics_path.write_text(
            json.dumps({"session": {"my_metric": {"value": float(score)}}}, indent=2),
            encoding="utf-8",
        )

        return StepOutput(
            data={"score": score},
            files=[metrics_path],
            metadata={"score": score},
        )
```

### Key rules

| Rule | Why |
|------|-----|
| Use `run_dir_from_ctx(ctx, config)` — not `config.get("run_dir")` | Reads `ctx.scope_context` (set by `per_run: true`). Falls back to config |
| Use `resolve_run_events(ctx, config)` — not `.run_ref` directly | Handles all storage modes: inline trace, symlinked `traces/`, and `.run_ref` cache indirection |
| Write outputs to `run_dir` | `collect_metrics` looks for `metrics.json` at `<run_dir>/metrics.json` |
| Never parse `events_path.parent.parent` to infer scenario | Use `ctx.scope_context.scenario` instead |

---

## Declaring the step in a pipeline YAML

### As a `run.post` hook (per-run)

```yaml
# pipelines/post-run.yaml
apiVersion: mas/v1
kind: Pipeline
metadata:
  name: my-post-run
spec:
  steps:
    - name: eval-booking
      # Path relative to experiment_yaml.parent (not the pipeline file's dir).
      type: lib/steps/my_eval.py:MyEvalStep
      per_run: true
      config:
        # Paths in config: are also resolved relative to experiment_yaml.parent.
        # Do NOT use ../datasets/... — that goes above the experiment dir.
        reference_path: datasets/reference/fares.yaml
```

```yaml
# experiment.yaml
experiment:
  run:
    n_runs: 3
    post:
      - ref: pipelines/post-run.yaml

  post:
    - ref: pipelines/post-experiment.yaml
```

> **Path resolution**: `base_dir` is always `experiment_yaml.parent`, for both
> the step type and paths in `config:`.
>
> | Value | Resolves to |
> |---|---|
> | `lib/steps/my_eval.py` | `<experiment_dir>/lib/steps/my_eval.py` (correct) |
> | `datasets/reference/fares.yaml` | `<experiment_dir>/datasets/reference/fares.yaml` (correct) |
> | `../lib/steps/my_eval.py` | one level above the experiment dir (wrong) |
> | `../datasets/reference/fares.yaml` | one level above the experiment dir (wrong) |

### Inline (no separate YAML file)

For a single step, declare it directly in `experiment.yaml`:

```yaml
experiment:
  run:
    post:
      - name: eval-booking
        type: lib/steps/my_eval.py:MyEvalStep
        per_run: true
        config:
          reference_path: datasets/reference/fares.yaml
```

---

## `register_steps.py` — standalone CLI registration

You run `mas-lab benchmark pipeline run pipelines/post-run.yaml -o ...` directly, not through `benchmark run`. Then the CLI loads the first `register_steps.py` it finds. It searches the pipeline file's directory and up to five parent directories.

Create one at the experiment root so the standalone pipeline CLI recognizes the type:

```python
# register_steps.py  (next to experiment.yaml)
import sys
from pathlib import Path

# Make lib/ importable as a package when using the standalone CLI.
_root = Path(__file__).resolve().parent   # experiment_dir
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from mas.lab.benchmark.pipeline import register_step
from lib.steps.my_eval import MyEvalStep

register_step("my_eval", MyEvalStep)
```

With the type registered, the standalone pipeline YAML can say `type: my_eval` instead of the file reference. `register_step_type` is a deprecated alias of `register_step`.

`register_steps.py` is not needed with `benchmark run`. `build_runtime_pipeline` always passes `base_dir=experiment_yaml.parent`.

---

## Downstream: aggregating per-run metrics

The step writes `metrics.json` in the MCE session schema:

```json
{
  "session": {
    "my_metric": { "value": 0.85 }
  }
}
```

`collect_metrics` reads this file automatically from the benchmark output folder:

```yaml
- name: collect-metrics
  type: collect_metrics
  config:
    output: results/tidy.csv
```

Downstream `compute_ci` and `plotnine` steps then reference `@collect-metrics`:

```yaml
- name: compute-ci
  type: compute_ci
  depends_on: [collect-metrics]
  config:
    data: "@collect-metrics"
    groupby: [scenario]
    metrics: [my_metric]
    extra_cols: [latency_s]
    output: results/ci.csv

- name: figure
  type: plotnine
  depends_on: [compute-ci]
  config:
    data: "@compute-ci"
    mapping: { x: latency_s_mean, y: mean, color: scenario }
    geom: point
    output: results/figure.png
```

Step output locations are relative to the level folder; never build absolute paths in a
custom step. Resolve them with the framework helper, which applies the default name, the
optional `subdir` / `filename` / relative `output` and rejects anything outside the folder:

```python
from mas.lab.benchmark.pipeline.artifact_paths import resolve_step_artifact_path

out = resolve_step_artifact_path(ctx, None, self.name, "tidy.csv", self.config,
                                 default_subdir="results")
```

---

## Deterministic evaluation pattern (no LLM judge)

Deterministic evaluation scores a structured tool call against the ground truth on the dataset item (`expectations.details`). Example for the trip planner's booking tool:

```yaml
# dataset.yaml item
expectations:
  details:
    origin: Celestia
    destination: Verdantia
    fare_class: standard
```

The step:

1. Loads `events.jsonl` via `resolve_run_events(ctx, config)`.
2. Finds `tool_call_start` events for the booking tool.
3. Extracts `origin`, `destination`, `fare_class` from the event arguments.
4. Compares against `expectations.details` with exact string matching (`str.strip().lower()`).
5. Writes `metrics.json` with `booking_correct: 1.0` or `0.0`.

The output format matches `eval_mce`. `collect_metrics` and `compute_ci` treat both evaluation modes the same.

Compared with MCE (`eval_mce`):

| | Deterministic evaluation | MCE (`eval_mce`) |
|---|---|---|
| Speed | ~1 ms / run | ~10–30 s / run (LLM call) |
| Cost | Zero | API credits |
| Reproducibility | 100% | Model-version dependent |
| Coverage | Structured tool calls | Free-form text output |

Use deterministic evaluation when the correct answer is unambiguous and arrives as a structured tool call. Use `eval_mce` for free-form quality metrics: goal success rate (GSR), groundedness, and response completeness.

---

## `pkg://` vs scheme syntax for tool refs

When referencing a library tool file in an overlay:

```yaml
# WRONG: importlib.resources.files("skills") resolves to a local
#   directory named skills/ if one exists (namespace package collision):
- ref: "pkg://skills/tools/run-skill-script.tool.yaml"

# CORRECT: entry-point scheme resolution via mas.runtime.manifest_libraries:
- ref: "skills:tools/run-skill-script.tool.yaml"
```

`pkg://NAME/path` calls `importlib.resources.files(NAME)`. That call resolves the Python package name directly. A local directory with that name on `sys.path` (for example `trip-planner/skills/`) becomes a namespace package. Then `pkg://` resolves to the local directory instead of the installed library.

`NAME:path` calls `mas.runtime.package_refs.resolve_library_scheme_root(NAME)`. That function resolves the library root through `mas.library_roots.resolve_named_library_root`. The lookup order is:

1. Lab-local libraries
2. Workspace config
3. Installed libraries
4. `MAS_LIBRARY_PATHS`
5. An ancestor `library.yaml` walk

The result is the library's real root, whatever local directories exist.

Rule: use the colon scheme (`skills:tools/...`, `standard:...`, `lab:...`) for tool refs from installed manifest libraries. Use `pkg://` only for direct Python package resources where no name collision is possible.

---

## Related

- [pipeline.md](pipeline.md)
- [pipeline-steps.md](pipeline-steps.md)
- [benchmark.md](benchmark.md)
