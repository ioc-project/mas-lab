<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tutorial 10 — Evaluation metrics

> **Packages:** `mas-lab`, `mas-library-eval` (metrics and the `eval_metric`
> plugin type), `mas-library-lab` (the `eval_mce` pipeline step)
> **Prerequisite:** [Tutorial 3](../03-experiments-and-analysis/README.md). You
> have scored a run with stock MCE, the Metrics Computation Engine: an
> open-source set of LLM-as-judge metrics.
> **Needs:** an LLM endpoint for the judge (see
> [Tutorial 0](../00-environment-setup/README.md)). Only `toy_echo` runs
> without one.

## Goal

Tutorial 3 asked one question: does this session look like a good answer?
Real labs ask more: did a tool fire, did two specialists contradict each
other, did a protocol activate? You score those too. Each answer is a session
score in `metrics.json`.

## What you need

- A finished run folder with `traces/events.jsonl` (from Tutorial 3).
- The judge LLM, the model that scores the run.

## Big picture

A metric is one named question about a run. It returns a number and a reason.
Evaluation is a post step: it reads the trace and writes `metrics.json` next
to `run_info.json`.

| Step | Source of the metric | Where it lives |
| --- | --- | --- |
| 1 | Stock MCE | Built into MCE. You list ids |
| 2 | Inline definition | `prompt_metrics` in the pipeline YAML |
| 3 | Registered plugin | `eval_metric` in a library |
| 4 | All three together | `eval-pipeline.yaml` |

Start with the cheapest source. Promote a metric only when several pipelines
use the same wording. Each id under `session` in `metrics.json` is one
question:

```json
{"schema_version": "1",
 "session": {"groundedness": {"value": 0.8, "reasoning": "…", "error": null}}}
```

`value` is a number or `null`; optional `details` holds structured extras.
Polarity is per id, so document it: `1.0` is not always good.

Each metric declares what input it needs, with two fields stored in
`details`:

| Field | Values | Meaning |
|---|---|---|
| `unit` | `mas`, `agent`, `call` | Whole MAS run, one agent, or one LLM or tool call (`call` is not implemented: one score per run) |
| `evidence` | `io`, `trajectory` | Only the input and output, or the ordered event history |

Stock MCE metrics see only the user query and the final answer, never the
trajectory. Attach pipelines explicitly; a `pipeline.yaml` next to the
experiment is not picked up:

```bash
mas-lab benchmark run EXPERIMENT.yaml --pipeline run:post:path/to/eval-pipeline.yaml
```

`run:post:` means "after each run"; the other levels are `item`, `scenario`
and `experiment` (see the [CLI reference](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/cli-reference.md)).
A relative path resolves from the directory of `experiment.yaml`.

---

## Step 1 — Score with stock MCE

List the ids you want. If you omit `metrics`, you get every session metric in
`METRIC_MAP`.

```yaml
- name: eval
  type: eval_mce
  per_run: true
  in: trace
  out: metrics
  config:
    metrics:
      - groundedness
      - goal_success_rate
      - context_preservation
```

Stock ids: `answer_relevancy`, `goal_success_rate`, `groundedness`,
`response_completeness`, `workflow_cohesion_index`, `workflow_efficiency`,
`consistency`, `context_preservation`, `information_retention`,
`intent_recognition_accuracy`, `component_conflict_rate`. MCE owns the
prompts. `mas-lab eval --list-metrics` lists these ids.

Try one outside a pipeline. `mas-lab eval --list-metrics` is offline. Scoring
needs the judge key:

```bash
mas-lab eval --list-metrics
export OPENAI_API_KEY=...
mas-lab eval docs/tutorials/03-experiments-and-analysis/fixtures/events.jsonl \
  --metric goal_success_rate --model gpt-4o
```

It prints one row per metric with a 0 to 1 score and the judge's reason. Add
`--recursive` to score every trace under a directory.

The runner reads `input_query` from the root `execution_start` and
`final_response` from the response agent. The judge model is the first hit in:

1. `eval_mce` `config.model` / `config.judge_model`
2. `experiment.evaluation.model`
3. `experiment.models.judge`
4. `experiment.model` / `models.main`
5. application `spec.models[]`

A runnable pin is in
[library-eval/examples/mce/judge-override/](https://github.com/outshift-open/mas-lab/tree/main/library-eval/examples/mce/judge-override).
See also [summarization.md — MCE judge model](../../manifests/summarization.md#mce-judge-model).

---

## Step 2 — Define an inline metric

MCE has no "did the agent invoke a tool" metric. You do not need a plugin: the
YAML is the metric, with an id, the evidence it needs and its prompt.

```yaml
config:
  metrics: [groundedness]
  prompt_metrics:
    - id: used_a_tool
      description: 1.0 if the run invoked at least one tool.
      unit: mas
      evidence: trajectory
      prompt: >
        Did the agent invoke at least one tool?
        Return JSON {"value": 1.0 or 0.0, "reasoning": "cite a tool_call_start event"}.
    - id: answered_the_user
      unit: mas
      evidence: io
      prompt: >
        Given only INPUT (the user query) and OUTPUT (the final answer),
        did OUTPUT address INPUT?
        Return JSON {"value": 1.0 or 0.0, "reasoning": "…"}.
```

Required keys: snake_case `id`, `prompt`, `unit`, `evidence`. Optional:
`description`, `system`, and `agent` (when `unit: agent`). The step appends
the evidence after `---` (`## INPUT` and `## OUTPUT`, or `## TRACE`) and
parses a JSON object with a numeric `value`, or `answer: YES|NO`. Put that
contract in your prompt. You should see `session.used_a_tool` in
`metrics.json`. Ids must not collide with stock MCE ids or a registered
`eval_metric`.

---

## Step 3 — Register a reusable metric

When several labs share the same wording, register it in a content library.
It lands on the existing MCE eval provider (`get_provider("mce")`), with no
second registry.

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
      provider: mce
```

```python
from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs

class ToyEchoMetric(EvalMetric):
    metric_id = "toy_echo"
    unit = "mas"
    evidence = "trajectory"
    requires_llm = False

    async def compute(self, inputs: RunInputs, ctx: MetricContext):
        path = inputs.require("native_trace")
        return {"value": 1.0, "reasoning": f"{path.name} is present",
                "error": None, "details": {"bytes": path.stat().st_size}}
```

A family of metrics can use `factory: mas.library.example.metrics:build_metrics`
instead of `module`/`class`. For an LLM judge, call
`mas.library.eval.metrics.llm_json.complete_json(messages)`. It uses the
OpenAI-compatible client `eval_mce` already installed
(`install_openai_llm_service`), shared with stock MCE and
`llm_service_config()` (for an external scorer): same credentials, retries
and proxy. Do not build a second HTTP client.

Rules:

- Duplicate ids raise an error at registration.
- Set `unit` and `evidence` on the class; the defaults are `mas` and
  `trajectory`.
- `batch_key` groups a family so one parse serves many ids (`compute_batch`).
- A failing metric writes `{value: null, error: "…"}` for that id only.
- For a second judge file such as `metrics_judge2.json`, split families across
  steps with distinct `metrics_filename` values.
- Prefix ids of a wrapped external scorer (for example
  `stateful_groundedness`) so they never collide with MCE ids.

List registered ids in `eval_mce.config.metrics` with stock ids and
`prompt_metrics`. Judge calls are not kernel `LLM_CALL` events, so the
runtime `llm_cache` does not replay them; only a caching gateway can.
`complete_json` retries malformed JSON once with a "JSON only" turn.

---

## Step 4 — Run the mixed pipeline

[eval-pipeline.yaml](eval-pipeline.yaml) mixes the three sources: stock
`groundedness`, registered `toy_echo` (ships with `mas-library-eval`, no LLM
needed) and inline `used_a_tool`. First validate it, with no LLM:

```bash
mas-lab benchmark pipeline validate docs/tutorials/10-evaluation-metrics/eval-pipeline.yaml
```

Then attach it to the Tutorial 3 experiment. The pipeline path resolves from
the experiment's directory, so use an absolute path or copy the file next to it.

```bash
mas-lab benchmark run docs/tutorials/03-experiments-and-analysis/experiment.yaml \
  --pipeline "run:post:$PWD/docs/tutorials/10-evaluation-metrics/eval-pipeline.yaml"
```

This calls the judge model, so it needs your key and costs tokens.

Each run folder now has a `metrics.json` with `groundedness`, `toy_echo` and
`used_a_tool` under `session`. An existing `metrics.json` is reused when the
trace, metric ids, definitions and judge model are unchanged. Add
`--set eval_mce.overwrite=true` to force re-scoring.

The step must run per run, inside a benchmark, because it finds
`traces/events.jsonl` through the run. To score one trace outside a benchmark,
use `mas-lab eval TRACE --metric goal_success_rate` (stock ids only).

---

## What you learned

1. A metric is a named question that writes one score under `session` in
   `metrics.json`. Its `unit` and `evidence` say what it reads.
2. Use stock ids first, an inline `prompt_metrics` entry for one experiment,
   and an `eval_metric` plugin for reuse.
3. Attach evaluation with `--pipeline run:post:...`. It runs per run, inside a
   benchmark.

## See also

- [Tutorial 3](../03-experiments-and-analysis/README.md): first experiment and MCE
- [LLM cache](../../manifests/llm-cache.md)
- [pipeline-steps.md](https://github.com/outshift-open/mas-lab/blob/main/lab/docs/pipeline-steps.md): `eval_mce`
- [library-eval README](https://github.com/outshift-open/mas-lab/blob/main/library-eval/README.md)

## Next

[Tutorial 11](../11-sessions-and-recovery/README.md): checkpoint, fork and
resume a conversation.
