<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Example: MCE judge model (`experiment.models` + `eval_mce.config.model`)

Eval feature example. Not a sample app.

There is **no committed global model in `config.yaml`**. Pin slots on the
experiment (`experiment.models`), the MAS, or the Agent. Scalar
`experiment.model` is shorthand for `models.main`. Omitted / `any` is filled
at engine time from local `config.yaml` `defaults.model`.

This example pins **turn vs judge** with the slot map, then a per-step
override:

- `experiment.models.main` — turn default (`gpt-4o`)
- `experiment.models.judge` — default for every `eval_mce` that omitted `config.model`
- per-step `config.model` — still wins
- `experiment.evaluation.model` — optional judge override (wins over `models.judge`)

Metric *prompts* live inside MCE (`mce_metrics_plugin`); this library does
not override them. Only the model id is configurable here.

| File | Role |
|------|------|
| `experiment.yaml` | Lab spec: `models.judge` + one strict step `config.model` |
| `mas.yaml` | Stub Agent (`spec.models: gpt-4o`) so `applications.manifest` resolves |

## Quickstart

```bash
mas-ctl validate library-eval/examples/mce/judge-override/experiment.yaml
```

To run for real, point `applications` at a MAS that produced traces, then:

```bash
mas-lab benchmark run library-eval/examples/mce/judge-override/experiment.yaml --progress
```

INFO logs: `EvalMceStep … judge model=… source=experiment.models.judge`
(or `eval_mce.config.model` on the strict step). Omit `models.judge` and
the source is `experiment.model` / `application.spec.models`.

## What is overridable

Existing `eval_mce` config (unchanged) plus **`model`**:

| Key | Default | Meaning |
|-----|---------|---------|
| `runs_dir` | pipeline output dir | Tree of `item*/r*/traces/events.jsonl` |
| `metrics` | all session MCE metrics | Subset to score |
| `overwrite` | `false` | Recompute `metrics.json` |
| `validate` | `true` | Schema-check artefacts |
| `max_workers` | `2` | Parallel judge calls |
| `fail_threshold` | `1.0` | Fraction of items that may fail |
| **`model`** | `experiment.models.judge` then `models.main` then application `spec.models[]` | LLM-as-judge model |

## Docs

- [summarization.md § MCE judge](../../../../docs/manifests/summarization.md#mce-judge-model)
- [experiment.md](../../../../docs/manifests/experiment.md)
- [pipeline-steps.md](../../../../lab/docs/pipeline-steps.md)
- Category: [MCE examples](../README.md)
