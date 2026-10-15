<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Custom pipeline steps

A **custom pipeline step** is a `PipelineStep` subclass defined in the
experiment's `lib/steps/` directory.  It integrates with the benchmark
lifecycle identically to built-in steps — it can be `per_run: true`, produce
artifacts, depend on other steps, and run inside `application.post` or
`run.post` hooks.

---

## When to write a custom step

Write one when:

- You need **deterministic fixture evaluation** (e.g. compare `run_action` tool
  calls against ground-truth labels) without a round-trip to an LLM judge.
- You need a **domain-specific plot** that standard `plotnine` config can't
  express.
- You need to call an external service or library that has no built-in step.

For everything else prefer built-in steps — `eval_mce`, `collect_metrics`,
`plotnine`, `extract_trace_stats`.

---

## Directory layout

```
my-experiment/
├── experiment.yaml
├── lib/
│   ├── __init__.py              # empty — makes lib/ a Python package
│   └── steps/
│       ├── __init__.py          # empty
│       ├── my_custom_step.py    # one or more PipelineStep subclasses
│       └── register_steps.py   # optional — registers types for standalone CLI use
├── pipelines/
│   ├── post-run.yaml            # per-run hooks
│   └── post-experiment.yaml    # experiment-level hooks
└── ...
```

The `lib/` directory lives next to `experiment.yaml`.  Its parent is
`experiment_yaml.parent`, which is the `base_dir` used by
`build_runtime_pipeline` when resolving file-path step types.

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
        # ── 1. Get the run directory from execution context ───────────────
        run_dir = run_dir_from_ctx(ctx, self.config)
        if run_dir is None:
            raise ValueError(
                f"Step '{self.name}': run scope unavailable. "
                "Declare with 'per_run: true'."
            )

        # ── 2. Resolve events.jsonl (handles .run_ref and inline traces) ──
        events_path = resolve_run_events(ctx, self.config)
        if events_path is None or not events_path.exists():
            logger.warning("Step '%s': no trace for %s — skipping", self.name, run_dir)
            return StepOutput(data={"skipped": True}, files=[], metadata={})

        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        # ── 3. Evaluate ───────────────────────────────────────────────────
        score = _my_score(events)

        # ── 4. Write metrics.json (MCE format — collect_metrics reads this) ──
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
| Use `run_dir_from_ctx(ctx, config)` — not `config.get("run_dir")` | Uses `ctx.scope_context` (set by `per_run: true`); degrades gracefully to config fallback |
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

  application:
    post:
      - ref: pipelines/post-experiment.yaml
```

> **Path resolution**: `base_dir` is always `experiment_yaml.parent`, for both
> the step type and paths in `config:`.
>
> | Value | Resolves to |
> |---|---|
> | `lib/steps/my_eval.py` | `<experiment_dir>/lib/steps/my_eval.py` ✓ |
> | `datasets/reference/fares.yaml` | `<experiment_dir>/datasets/reference/fares.yaml` ✓ |
> | `../lib/steps/my_eval.py` | one level above the experiment dir ✗ |
> | `../datasets/reference/fares.yaml` | one level above the experiment dir ✗ |

### Inline (no separate YAML file)

For a single step you can inline it directly in `experiment.yaml`:

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

When running `mas-lab benchmark pipeline run pipelines/post-run.yaml -o ...`
**directly** (not via `benchmark run`), the CLI calls `_load_lab_custom_steps`
which searches upward from the pipeline file for `register_steps.py`.

Create one so the type is recognized by the standalone pipeline CLI:

```python
# lib/steps/register_steps.py
import sys
from pathlib import Path

# Make lib/ importable as a package when using the standalone CLI.
_lib_root = Path(__file__).resolve().parents[2]   # experiment_dir
if str(_lib_root) not in sys.path:
    sys.path.insert(0, str(_lib_root))

from mas.lab.benchmark.pipeline import register_step_type
from lib.steps.my_eval import MyEvalStep

register_step_type("my_eval", MyEvalStep)
```

> `register_steps.py` is **not needed** when running via `benchmark run`
> because `build_runtime_pipeline` always passes `base_dir=experiment_yaml.parent`.

---

## Downstream: aggregating per-run metrics

`metrics.json` written by the step uses the MCE session schema:

```json
{
  "session": {
    "my_metric": { "value": 0.85 }
  }
}
```

`collect_metrics` picks this up automatically when its `output_dir` points at
the benchmark root:

```yaml
- name: collect-metrics
  type: collect_metrics
  config:
    output: "{output_dir}/results/tidy.csv"
```

Downstream `compute_ci` and `plotnine` steps can then reference `@collect-metrics`:

```yaml
- name: compute-ci
  type: compute_ci
  depends_on: [collect-metrics]
  config:
    data: "@collect-metrics"
    groupby: [scenario]
    metrics: [my_metric]
    extra_cols: [latency_s]
    output: "{output_dir}/results/ci.csv"

- name: figure
  type: plotnine
  depends_on: [compute-ci]
  config:
    data: "@compute-ci"
    mapping: { x: latency_s_mean, y: mean, color: scenario }
    geom: point
    output: "{output_dir}/results/figure.png"
```

---

## Deterministic evaluation pattern (no LLM judge)

Deterministic evaluation scores a structured tool call against the
app-specific ground truth on the Dataset item (`expectations.details`), e.g.
for the trip planner's booking tool:

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

This produces the same output format as `eval_mce` so `collect_metrics` and
`compute_ci` work identically for both evaluation modes.

**Advantages over MCE (`eval_mce`):**

| | Deterministic evaluation | MCE (`eval_mce`) |
|---|---|---|
| Speed | ~1 ms / run | ~10–30 s / run (LLM call) |
| Cost | Zero | API credits |
| Reproducibility | 100% | Model-version dependent |
| Coverage | Structured tool calls | Free-form text output |

Use deterministic evaluation when the task has an unambiguous correct answer that is
expressed as a structured tool call.  Use `eval_mce` for free-form quality
metrics (goal success rate, groundedness, response completeness).

---

## `pkg://` vs scheme syntax for tool refs

When referencing a library tool file in an overlay:

```yaml
# ✗ Wrong — importlib.resources.files("skills") resolves to a local
#   directory named skills/ if one exists (namespace package collision):
- ref: "pkg://skills/tools/run-skill-script.tool.yaml"

# ✓ Correct — entry-point scheme resolution via mas.runtime.manifest_libraries:
- ref: "skills:tools/run-skill-script.tool.yaml"
```

`pkg://NAME/path` calls `importlib.resources.files(NAME)` which resolves the
**Python package name** directly.  If a local directory with that name exists
on `sys.path` (e.g. `trip-planner/skills/`), Python treats it as a namespace
package and `pkg://` resolves to the local directory instead of the installed
library.

`NAME:path` uses `mas.runtime.package_refs.resolve_library_scheme_root(NAME)`,
which resolves the library root via `mas.library_roots.resolve_named_library_root`
(lab-local libraries, workspace config, installed libraries, `MAS_LIBRARY_PATHS`,
then an ancestor `library.yaml` walk) — always resolving to the library's real
root regardless of local directory names.

**Rule**: use `skills:tools/...`, `standard:...`, `lab:...` etc. (colon scheme)
for tool refs that come from installed manifest libraries.  Reserve `pkg://`
for direct Python package resource access where no name collision is possible.

---

## Related

- [pipeline.md](pipeline.md)
- [pipeline-steps.md](pipeline-steps.md)
- [benchmark.md](benchmark.md)
