---
sidebar: related
---

<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS Lab paper

*MAS-Lab: A Specification-Driven Validation Framework for Reliable Multi-Agent
Systems*

**Paper:** [arXiv:2606.30546](https://arxiv.org/abs/2606.30546)
([PDF](https://arxiv.org/pdf/2606.30546))

The article describes the specification-driven model behind MAS Lab. Its
**Section 5** reports experiments. This repository reproduces them as three
runnable labs. A lab is a folder of experiments.

---

## What you can reproduce

| Lab | Paper section | What it explores |
|-----|---------------|------------------|
| `design-space.lab` | §5.1 | Design patterns and team topologies |
| `lifecycle-control.lab` | §5.2 | Governance and lifecycle control |
| `extensions.lab` | §5.3 | Memory and context overlays |

Source under [`labs/`](https://github.com/ioc-project/mas-lab/tree/main/labs) in the repository.

Each lab holds one or more experiments. `design-space.lab` has two.
`extensions.lab` has extra fact-recall variants.

Running an experiment also produces its charts, metrics, and comparison reports.
Pipeline steps generate them, so you need no plotting scripts. The per-lab
READMEs under [`labs/`](https://github.com/ioc-project/mas-lab/tree/main/labs)
describe the scenarios, datasets, and output files. Absolute numbers depend on
the model you use. Labs vs libraries: [labs-and-libraries.md](../labs-and-libraries.md).

New to experiments? Start with [Tutorial 3 — Run an experiment](../tutorials/03-experiments-and-analysis/README.md).

---

## Quick reproduction

After [Tutorial 0](../tutorials/00-environment-setup/README.md) (install and LLM
access), from the repository root:

```bash
task reproduce
```

This needs [go-task](https://taskfile.dev/). It runs all Section 5 experiments
in sequence. Without `task`, run them one by one:

```bash
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml --progress
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml --progress
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
mas-lab benchmark run labs/extensions.lab/experiment.yaml --progress
```

Add `--dry-run` to validate an experiment without calling a model. The Letta
scenarios of `extensions.lab` need their own virtual environment. See its
[README](https://github.com/ioc-project/mas-lab/blob/main/labs/extensions.lab/README.md).

Completed runs are cached. If inputs are unchanged, a re-run refreshes the
reports without new LLM calls. Output locations: [User configuration](../user-config.md).

---

## Cite MAS Lab

If you use the framework, benchmarks, or methodology, cite the paper, not only
this repository.

**Authors:** Jordan Augé, Giovanna Carofiglio, Giulio Grassi, Jacques Samain
(Cisco Systems / Outshift)

**arXiv:** [2606.30546](https://arxiv.org/abs/2606.30546)
([PDF](https://arxiv.org/pdf/2606.30546))

```bibtex
@article{maslab2026,
  title         = {{MAS-Lab}: A Specification-Driven Validation Framework for Reliable Multi-Agent Systems},
  author        = {Aug{\'e}, Jordan and Carofiglio, Giovanna and Grassi, Giulio and Samain, Jacques},
  year          = {2026},
  eprint        = {2606.30546},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2606.30546}
}
```

---

## See also

- [Home](../index.md)
- [Labs](../labs.md): per-lab detail and status
- [Tutorial 0 — Environment setup](../tutorials/00-environment-setup/README.md)
- [Tutorials](../tutorials/index.md)
- [Open standards](../standards/index.md)
- [References](../references/index.md)
- [Blog](../blog/index.md)

Next: [Tutorial 3 — Run an experiment](../tutorials/03-experiments-and-analysis/README.md).
