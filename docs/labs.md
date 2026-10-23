<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Paper labs

These are the three labs shipped in this repository. They reproduce the
experiments of the MAS-Lab paper. For the IoC motivation, protocol-evaluation,
and cognitive-challenge labs, start at the [Discover → Labs](discover/labs.md)
index.

A **lab** is a folder named `*.lab/` that holds one experiment. Its
`experiment.yaml` lists three things:

- the **scenarios**: named setups, each a stack of overlays
- the **dataset**: the items to run
- the **analysis steps**: they build charts and tables from the `events.jsonl`
  files, so a lab needs no separate plotting scripts

```bash
mas-lab benchmark run labs/<name>.lab/experiment.yaml --progress
```

A run has two phases:

1. **Execution.** MAS Lab runs every scenario on every dataset item, `n_runs`
   times each. Each run writes an `events.jsonl`. Work already in the trace cache
   is skipped.
2. **Analysis.** MAS Lab runs the analysis steps declared in the same file.

See also the [labs index](https://github.com/ioc-project/mas-lab/tree/main/labs),
[labs vs libraries](labs-and-libraries.md) and the
[experiment manifest](manifests/experiment.md).

## The paper labs

MAS Lab ships **three reproducible labs**. Each one reproduces an experiment
section of the paper *MAS-Lab: A Specification-Driven Validation Framework for
Reliable Multi-Agent Systems* (see the [paper page](paper/index.md)). The
[labs README](https://github.com/ioc-project/mas-lab/blob/main/labs/README.md)
lists the run commands.

| Lab | Paper § | What it explores |
|---|---|---|
| [`design-space.lab`](https://github.com/ioc-project/mas-lab/tree/main/labs/design-space.lab) | §5.1 | Reasoning patterns and team layouts |
| [`lifecycle-control.lab`](https://github.com/ioc-project/mas-lab/tree/main/labs/lifecycle-control.lab) | §5.2 | Governance **overlays** across the lifecycle |
| [`extensions.lab`](https://github.com/ioc-project/mas-lab/tree/main/labs/extensions.lab) | §5.3 | Memory and context **overlays** |

All three change **overlays** only: a pattern, a layout, a policy or a memory. They
never change the application. That is the point: each change can be made,
audited and measured on its own.

### Lab 1: Design space (§5.1)

**Question.** Can you try reasoning strategies and team layouts by editing a YAML
file, without touching agent code?

Two experiments use the same analysis. Each measures goal success rate (GSR),
answer relevancy (AR) and session latency. Only the overlay differs between
scenarios.

| Experiment | What changes | System | Dataset | Scenarios |
|---|---|---|---|---|
| **1.1** design patterns | A reasoning-pattern overlay | One QA agent | 100 reasoning queries | Chain-of-Thought, ReAct, Plan-and-Execute, Reflection, Tree-of-Thoughts |
| **1.2** topologies | A team-layout overlay | The four-agent trip planner | 250 trip-planner prompts | Parallel, linear pipeline, moderator-broker, supervised, verifier |

!!! example "A result worth knowing"
    In experiment 1.1, Plan-and-Execute writes near-perfect text (AR about 0.999)
    but almost never completes the task (GSR about 0.03). Its answers look
    plausible and do not solve the problem. A text-quality score alone would have
    ranked it first. Experiment 1.2 makes no claim about which layout wins. It
    gives you the means to compare them.

Each experiment writes one figure (`figure-01-overhead-quality.png` and
`figure-02-overhead-quality.png`) in its `results/` folder.

### Lab 2: Lifecycle control (§5.2)

**Question.** What does governance cost, and what does it catch?

The main experiment adds governance overlays one at a time: a baseline, then a
budget, then a guardrail, then a production profile. It then evaluates a unified
governance policy model. It builds three figures: the overhead per call, the policy
density and a fault-detection heatmap.

A second experiment, `02-bit-exactness`, checks that a topology reproduces
exactly, using lab-local overlays. `task reproduce` does not run it.

!!! note "Scope"
    The overlays use the `native` observability plugin only. Export to
    OpenTelemetry and the knowledge-graph pipelines are not part of this lab. The
    lab lists semantic PII judging, homoglyph robustness and paired significance
    tests as future work.

### Lab 3: Extensions (§5.3)

**Question.** Is adding memory an overlay change, not an application change?

The experiment compares four scenarios on the trip planner:

| Scenario | What it adds |
|---|---|
| `baseline` | Tools only |
| `with-vector-memory` | Vector memory |
| `with-letta-memory` | Letta memory |
| `with-vector-memory-guardrail` | Vector memory and a guardrail overlay |

The claim under test is that context sources are typed contracts.

The Letta scenarios need the `letta` package. It conflicts with the dependencies of
`mas-library-skills[all]`, so it gets its own virtual environment:

```bash
task install-lab LAB=labs/extensions.lab
```

!!! warning "Reproduction status"
    The lab's `RESULTS.md` records what its last full run showed and what it did not.

    - **Shown.** 120 runs completed (4 scenarios, 30 runs each). Governance fired
      only where the overlay says it should. All three denials are in the guardrail
      scenario, on the one query that names a forbidden destination. The other 117
      runs have none. You can read this from the trace, with no model judge.
    - **Not shown.** The memory overlays produced **no** `memory_*` events. The
      overlay set up the plugin and registered the search tool, but the agent never
      called it. The memory claim holds only on items that force the agent to recall
      a profile attribute.
    - **Not refreshed.** The judge-scored GSR and AR numbers for §5.3 were not
      regenerated, because the judge step hung. The previously published table is
      still the reference.

### Named experiments

| `experiment.name` | Output path |
|---|---|
| `lab1-exp1.1-design-patterns-qa` | `$XDG_DATA_HOME/mas/labs/lab1-exp1.1-design-patterns-qa/` |
| `lab1-exp1.2-topologies-trip-planner` | `$XDG_DATA_HOME/mas/labs/lab1-exp1.2-topologies-trip-planner/` |
| `lifecycle-control` | `$XDG_DATA_HOME/mas/labs/lifecycle-control/lifecycle-control/` |
| `memory-extension-reproducibility` | `$XDG_DATA_HOME/mas/labs/memory-extension-reproducibility/` |

`task reproduce` runs these four experiments. The first lab holds two of them.
`mas-lab config` prints where your machine stores outputs and the trace cache.

## Running them

The labs run over the sample applications shipped with MAS Lab. Run them from a
checkout of the repository, after
[Tutorial 0](tutorials/00-environment-setup/README.md) has set up the install and a
model endpoint.

```bash
task reproduce                                   # all four experiments
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --dry-run
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml \
  --override 'experiment:experiment.dataset.limit=5' \
  --max-runs 1 --progress                        # quick smoke: five items, one run each
mas-lab benchmark show last plots
```

To keep a smaller run, set `dataset.limit` in the experiment. Do not keep a trimmed
copy of the dataset. Finished runs are cached, so a rerun refreshes the figures
without new model calls.

See the [paper page](paper/index.md) and the [CLI overrides](cli/overrides.md).

## Writing your own lab

A lab folder holds a `lab-config.yaml`, an `experiment.yaml`, datasets, overlays
and, if needed, custom analysis steps. Keep it flat. Put reusable applications and
plugins in a *library* next to it, and refer to them as `name:path`.

1. Build the agent and the MAS you want to study:
   [Tutorial 1](tutorials/01-building-an-agent/README.md),
   [Tutorial 2](tutorials/02-creating-a-mas/README.md).
2. Write one overlay per scenario. Each overlay changes one factor.
3. Declare the scenarios, the dataset, `n_runs` and the analysis steps:
   [Tutorial 3](tutorials/03-experiments-and-analysis/README.md).
4. Choose or write the metrics:
   [Tutorial 10](tutorials/10-evaluation-metrics/README.md).

See [labs vs libraries](labs-and-libraries.md), the
[dataset manifest](manifests/dataset.md) and the
[pipeline manifest](manifests/pipeline.md).
