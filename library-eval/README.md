<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-eval

Evaluation library for MAS Lab — quality metrics and LLM-as-judge scoring using **MCE** (metrics-computation-engine).

## Architecture

This library provides:

1. **Library code** (`mas.library.eval.*`): MCE integration, session entity construction, metric computation
2. **Pipeline steps** (`mas.library.eval.steps`): Evaluation steps for benchmark pipelines
3. **CLI component** (`mas.library.eval.cli`): Auto-registered `mas-lab eval` command

When installed, `mas-library-eval` automatically registers the `mas-lab eval` command via the `mas.lab.cli.components` entry point.

## EvalMetrics on the MCE provider

Libraries register session metrics onto the existing MCE eval provider
through the `eval_metric` plugin type (`EvalProvider.register_metric`).
`eval_mce` then runs any mix of stock MCE ids and registered ids into one
`metrics.json`. Each metric knows what input it needs (`unit` / `evidence`
on the metric). Stock MCE ids are MAS I/O, not the raw trajectory.

```yaml
# library.yaml
types:
  - eval_metric
plugins:
  - type: eval_metric
    name: toy_echo
    module: mas.library.eval.metrics.toy
    class: ToyEchoMetric
    attributes:
      provider: mce          # default
  # A family that yields many ids from one factory:
  - type: eval_metric
    name: my_family
    factory: mas.library.example.metrics:build_metrics
    attributes:
      provider: mce
```

- Duplicate metric ids raise at registration.
- Stock MCE session ids are `EvalMetric`s on `get_provider("mce")` (MAS I/O, one batch per run).
- Plugins and inline `prompt_metrics` run through the same `compute_metrics` engine.
- Unknown ids error with the list of available ids.
- Metrics that share `batch_key` are computed once per run via `compute_batch`.
- A failing metric writes `{value: null, error: "..."}` for that id only.
- Optional `details` on each score is additive in `metrics.json` schema v1.

List registered ids in an `eval_mce` step `metrics:` list, or split families
across steps with distinct `metrics_filename` values (`metrics.json`,
`metrics_judge2.json`). One-off LLM-as-judge questions can be added on the
same step as `prompt_metrics: [{id, prompt, unit, evidence}]` without a
plugin (ids must not collide with stock MCE or a registered metric).
`collect_metrics` and `metrics_to_dataframe` accept a filename, a glob, or
a list, and copy `details` into a JSON column.

How the pieces fit (stock MCE, prompts, plugins, judge infra, trajectory
wrappers): [Tutorial 10](../docs/tutorials/10-evaluation-metrics/).

## Installation

```bash
# From workspace root
cd library-eval && uv pip install -e .

# Verify installation
mas-lab eval --list-metrics
```

The `mas-lab eval` command is automatically available after installation — no manual CLI registration needed.

## Dependencies

- **metrics-computation-engine** — MCE core (public package from telemetry-hub)
- **mce_metrics_plugin** — Quality metrics plugin (GoalSuccessRate, Groundedness, ResponseCompleteness, etc.)

## Package Structure

```
src/mas/library/eval/
├── __init__.py
├── evaluator.py               # EvalProvider + get_provider("mce")
├── providers/mce.py           # Built-in MCE provider (stock + registered metrics)
├── mce/
│   ├── catalog.py             # One catalog: METRIC_MAP + METRIC_REGISTRY
│   ├── runner.py              # Native-trace MCE/deepeval compute
│   └── registry_api.py        # SessionEntity CamelCase API
├── metrics/                   # EvalMetric, plugins, prompt_metrics, stock wrappers
├── steps/                     # Legacy CamelCase EvalMceStep
└── cli/                       # mas-lab eval
```

## CLI Component Integration

This library uses the `mas-lab` CLI extension system. When installed, it automatically registers the `eval` command:

**Entry point** (`pyproject.toml`):

```toml
[project.entry-points."mas.lab.cli.components"]
eval = "mas.library.eval.cli:EvalCliComponent"
```

**Component class** (`mas.library.eval.cli`):

```python
class EvalCliComponent:
    def register(self, app: click.Group) -> str:
        """Register 'eval' command on mas-lab CLI."""
        app.add_command(eval_cmd, name="eval")
        return "eval"
```

After installation, the command is immediately available:

```bash
mas-lab eval --help
```

## MCE vs MCE v2

| Feature | MCE (this component) | MCE v2 (proprietary extension) |
|---------|------------------------|------------------------------|
| Package | `metrics-computation-engine` | (not shipped here) |
| Input | OTEL spans (jsonl) | Custom trace format |
| Metrics | Native + mce_metrics_plugin | mce.providers.native.metrics |
| LLM setup | `LLMJudgeConfig` + `Jury` | `LLMService` (patched) |
| API | `async compute(SessionEntity)` | `compute(resource_id, context)` |
| Status | ✅ Public, stable | ⚠️  Private, complex |

## Usage

### Standalone CLI

```bash
# Score a single trace
mas-lab eval path/to/events.jsonl --metric goal_success_rate --metric groundedness

# Batch scoring over a benchmark output tree
mas-lab eval path/to/experiment/ --metric goal_success_rate --recursive
```

### In Benchmarks

```yaml
# experiment.yaml
pipeline:
  - step: run-mas
    # ... execution config
  - step: eval-mce
    metrics:
      - goal_success_rate
      - groundedness
      - response_completeness
    model: azure/gpt-4o
    api_key_env: OPENAI_API_KEY
```

### Programmatic

```python
from mas.library.eval.mce import compute_session_metrics, build_session_entity_from_trace

# Load trace
session_entity = build_session_entity_from_trace("path/to/events.jsonl")

# Compute metrics
results = await compute_session_metrics(
    session=session_entity,
    metrics=["GoalSuccessRate", "Groundedness"],
    llm_config={
        "LLM_MODEL_NAME": "azure/gpt-4o",
        "LLM_BASE_MODEL_URL": "https://api.openai.com/v1",
        "LLM_API_KEY": os.environ["OPENAI_API_KEY"],
    },
)
```

## Migration from MCE v2

**Before (MCE v2, broken)**:

```python
from mas.library.eval.mce import install_openai_llm_service, compute_session_metrics
install_openai_llm_service(model_override="azure/gpt-4o")
results = compute_session_metrics(trace_path, ["goal_success_rate"])
```

**After (MCE, this component)**:

```python
from mas.library.eval.mce import compute_session_metrics, build_session_from_trace
session = build_session_from_trace(trace_path)
results = await compute_session_metrics(session, ["GoalSuccessRate"], llm_config)
```

## Metric Names

CLI ``--metric``, YAML ``metrics:``, and ``metrics.json`` keys are **snake_case**.
CamelCase is the plugin class name used by the SessionEntity API.

| Id (CLI / YAML) | Class name |
|-----------------|------------|
| `goal_success_rate` | `GoalSuccessRate` |
| `groundedness` | `Groundedness` |
| `response_completeness` | `ResponseCompleteness` |
| `answer_relevancy` | `AnswerRelevancy` |

## Configuration

LLM config is provided via `LLMJudgeConfig`:

```python
from metrics_computation_engine.models.requests import LLMJudgeConfig

llm_config = LLMJudgeConfig(
    LLM_MODEL_NAME="azure/gpt-4o",
    LLM_BASE_MODEL_URL="https://api.openai.com/v1",
    LLM_API_KEY=os.environ["OPENAI_API_KEY"],
)
```

Or as a dict:

```python
llm_config = {
    "LLM_MODEL_NAME": "azure/gpt-4o",
    "LLM_BASE_MODEL_URL": "https://api.openai.com/v1",
    "LLM_API_KEY": os.environ["OPENAI_API_KEY"],
}
```

## See Also

- Feature example (not a sample app): [examples/mce/judge-override/](examples/mce/judge-override/)
- [summarization.md § MCE judge](../docs/manifests/summarization.md#mce-judge-model)
- [MCE documentation](https://github.com/agntcy/telemetry-hub/tree/main/metrics_computation_engine)
- [mce_metrics_plugin](https://github.com/agntcy/telemetry-hub/tree/main/metrics_computation_engine/plugins/mce_metrics_plugin)
- [Tutorial: Output Quality Evaluation](../../docs/tutorial-evaluation.md)
