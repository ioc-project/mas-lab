<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Designing experiments

Structure an **experiment manifest** (`experiment.yaml`) for ablations, smoke
validation, and publishable results.

Terms: [glossary.md](../../docs/glossary.md).

## One canonical experiment file

Use CLI flags for smoke — do not fork `experiment-smoke.yaml` unless documented:

```bash
mas-lab benchmark run experiment.yaml --dry-run
mas-lab benchmark run experiment.yaml --limit-scenarios 1 --max-runs 1 --progress
```

## Scenario matrix

Each **scenario** is one column: a setup `id` and layered **overlays**:

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

Hold **dataset** and `n_runs` constant across scenarios.

Details: [multi-scenario-format.md](multi-scenario-format.md).

## Repeats (`n_runs`)

```yaml
  run:
    n_runs: 3
```

Use `n_runs > 1` when **pipeline steps** report confidence intervals.

A failed scenario×item×run is **reattempted** by default
(`execution.max_attempts: 3`, independent of `n_runs`). That covers LLM HTTP
failures after in-call retries are spent. Auth and TLS failures are
not retried. Set `max_attempts: 1` to fail the slot on the first error.

## Level hooks for figures

Declare figures on experiment-level `post:` (CLI `--depth exp`), not a
`pipeline:` key:

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

Record `experiment.metadata` for publication: see [Experiments and analysis](../../docs/tutorials/03-experiments-and-analysis/README.md).
