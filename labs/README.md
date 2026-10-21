<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Labs

This page lists the labs and shows how to run them. Read it to regenerate the figures of paper §5. You need `mas-lab` installed and an OpenAI-compatible endpoint.

Smallest working command:

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
```

You should see a progress bar. Then figures and CSV files appear under `$XDG_DATA_HOME/mas/labs/<experiment-name>/results/`.

A **lab** is a `*.lab/` folder with an `experiment.yaml`. The file lists scenarios (setups to compare), a dataset (inputs), and level hooks (`run`, `item`, `scenario`, and experiment `post:`). The hooks build figures from `events.jsonl` logs.

Terms: [glossary.md](../docs/glossary.md). Lab vs library: [labs-and-libraries.md](../docs/labs-and-libraries.md).

## Generate figures

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
mas-lab benchmark run labs/extensions.lab/experiment.yaml --progress
# design-space.lab holds two experiments, one per sub-folder:
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml --progress
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml --progress
```

Two flags help while you try things out:

- `--dry-run` validates an experiment without calling a model.
- `--override 'experiment:experiment.dataset.limit=5'` caps the dataset for a smoke run ([CLI overrides](../docs/cli/overrides.md)).

Quickstart: [lab/docs/labs-quickstart.md](../lab/docs/labs-quickstart.md). Paper map: [docs/paper/index.md](../docs/paper/index.md). Per-lab detail and status: [docs/labs.md](../docs/labs.md).

## Labs

| Lab | Paper § | Focus |
|-----|---------|-------|
| [design-space.lab](design-space.lab/README.md) | §5.1 | Design patterns + topologies |
| [lifecycle-control.lab](lifecycle-control.lab/README.md) | §5.2 | Governance **overlays** across lifecycle |
| [extensions.lab](extensions.lab/) | §5.3 | Memory **overlays** (see the [README](extensions.lab/README.md)) |

## Run all

```bash
task reproduce
```

This runs the four experiments above in sequence. It requires [go-task](https://taskfile.dev/) and an LLM endpoint, or a populated trace cache.

## Output paths

| `experiment.name` | Typical path |
|-------------------|--------------|
| `lab1-exp1.1-design-patterns-qa` | `$XDG_DATA_HOME/mas/labs/lab1-exp1.1-design-patterns-qa/` |
| `lab1-exp1.2-topologies-trip-planner` | `$XDG_DATA_HOME/mas/labs/lab1-exp1.2-topologies-trip-planner/` |
| `lifecycle-control` | `$XDG_DATA_HOME/mas/labs/lifecycle-control/lifecycle-control/` |
| `memory-extension-reproducibility` | `$XDG_DATA_HOME/mas/labs/memory-extension-reproducibility/` |

```bash
mas-lab benchmark show last plots
```

## Extend

See [lab/docs/labs-going-further.md](../lab/docs/labs-going-further.md) and [docs/labs-and-libraries.md](../docs/labs-and-libraries.md).

## See also

- [docs/paper/index.md](../docs/paper/index.md)
- [cli/observability.md](../docs/cli/observability.md)
