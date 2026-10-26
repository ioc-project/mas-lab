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

## Reproducibility

The experiments of Section 5 ship in this repository as three labs:
`design-space.lab` (§5.1), `lifecycle-control.lab` (§5.2) and `extensions.lab`
(§5.3). The [paper labs](../discover/labs.md#paper-labs) section describes each
one. [Running them](../discover/labs.md#running-them) gives the commands, including
`task reproduce`, and where the outputs go. New to experiments? Start with
[Tutorial 3 — Run an experiment](../tutorials/03-experiments-and-analysis/README.md).

The IoC labs, which are not part of the paper, are published as Hugging Face
datasets with their recorded runs and scores:

- [ioc-motivation-labs](https://huggingface.co/datasets/ioc-project/ioc-motivation-labs):
  how multi-agent systems fail; the companion of the
  [Outshift blog post](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems)
- [ioc-eval-labs](https://huggingface.co/datasets/ioc-project/ioc-eval-labs):
  the ACCORD and CONCORD evaluation

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
- [Labs](../discover/labs.md#paper-labs): per-lab detail and status
- [Tutorial 0 — Environment setup](../tutorials/00-environment-setup/README.md)
- [Tutorials](../tutorials/index.md)
- [Open standards](../standards/index.md)
- [References](../references/index.md)
- [Blog](../blog/index.md)

Next: [Tutorial 3 — Run an experiment](../tutorials/03-experiments-and-analysis/README.md).
