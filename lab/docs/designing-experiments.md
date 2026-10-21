<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Designing experiments

This page shows how to structure an **experiment manifest** (`experiment.yaml`). Read it before you design an ablation, a smoke check, or a publishable study.

Smallest working command: `mas-lab benchmark run experiment.yaml --dry-run`.

Terms: [glossary.md](../../docs/glossary.md).

## One canonical experiment file

Use CLI flags for smoke runs. Do not fork an `experiment-smoke.yaml` unless you document it.

```bash
mas-lab benchmark run experiment.yaml --dry-run
mas-lab benchmark run experiment.yaml --limit-scenarios 1 --max-runs 1 --progress
```

## Scenario matrix

Each **scenario** is one column of the matrix. It has a setup `id` and layered **overlays** (YAML patches applied on top of the base MAS):

```yaml
experiment:
  scenarios:
    - id: baseline
      overlays: {logic: [], control: [], infra: []}
    - id: with-guardrail
      overlays:
        logic: []
        control: [with-guardrail]
        infra: []
```

Keep the **dataset** and `n_runs` constant across scenarios.

Details: [multi-scenario-format.md](multi-scenario-format.md).

## Repeats (`n_runs`)

```yaml
  run:
    n_runs: 3
```

Use `n_runs > 1` when **pipeline steps** report confidence intervals.

The scheduler reattempts a failed scenario×item×run by default. The default is `schedule.max_attempts: 3`, independent of `n_runs`. The older `execution:` block is deprecated.

Reattempts cover LLM HTTP failures after in-call retries run out. Auth and TLS failures are not retried. Set `max_attempts: 1` to fail the slot on the first error.

## Level hooks for figures

Declare figures in the experiment-level `post:` list (CLI `--depth exp`). There is no `pipeline:` key:

```yaml
  post:
    - name: figure-overhead-quality
      type: plotnine
      depends_on: [gather-experiment, compute-ci]
      config:
        output: results/figure-02-overhead-quality.png
```

## Example labs

| Study | Lab |
|-------|-----|
| Design patterns | [design-space.lab/01-design-patterns](../../labs/design-space.lab/01-design-patterns/) |
| Topologies | [design-space.lab/02-topologies](../../labs/design-space.lab/02-topologies/) |
| Lifecycle / governance | [lifecycle-control.lab](../../labs/lifecycle-control.lab/) |
| Memory **overlays** | [extensions.lab](../../labs/extensions.lab/) |

To record `experiment.metadata` for publication, see [Experiments and analysis](../../docs/tutorials/03-experiments-and-analysis/README.md).
