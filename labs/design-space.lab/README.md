<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lab 1 — Design Space Exploration

This lab compares reasoning strategies and coordination topologies by editing YAML only. Read it to run the paper §5.1 experiments.

Paper section: §5.1. Tutorial: [docs/tutorials/03-experiments-and-analysis/](../../docs/tutorials/03-experiments-and-analysis/).

## Running the experiments

Run from the repository root. You need `uv`, Python 3.11+, and an OpenAI-compatible endpoint (see [docs/tutorials/00-environment-setup/README.md](../../docs/tutorials/00-environment-setup/README.md)).

```bash
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml --progress
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml --progress

# Quick smoke: first 5 dataset items, one run each
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml \
  --override 'experiment:experiment.dataset.limit=5' --max-runs 1 --progress

# Validate the configuration and print the plan without calling a model
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml --dry-run
```

`--override` is documented in [CLI overrides](../../docs/cli/overrides.md). `--max-runs` overrides `run.n_runs`.

Results and figures go to `$XDG_DATA_HOME/mas/labs/<experiment-name>/results/` (see `mas-lab config`). Each experiment writes one figure, named in its section below.

---

## What the lab tests

The research question: can you explore reasoning strategies and coordination topologies by editing a YAML file, without touching agent code?

Two experiments run against the same pipeline:

| Exp | What varies | Agent | Dataset | Scenarios |
|-----|-------------|-------|---------|-----------|
| [1.1 — design-patterns-qa](01-design-patterns/) | Design pattern overlay | Single QA agent | 100 reasoning queries | CoT, ReAct, Plan-Execute, Reflection, ToT |
| [1.2 — topologies-trip-planner](02-topologies/) | Topology overlay | 4-agent trip-planner | 250 trip-planner prompts (`trip-planner-benchmark`) | parallel, linear-pipeline, moderator-broker, supervised, verifier |

Both measure goal success rate (GSR), answer relevancy (AR), and session latency under identical conditions. GSR is the share of tasks the system completes. AR scores how relevant the answer text is to the question. Only the declared overlay changes between scenarios. An overlay is a YAML patch applied on top of the base agent or MAS.

---

## Exp 1.1 — Design patterns on a QA agent

**What it tests.** Five reasoning-strategy overlays run on the same single-agent QA binary and the same 100-item dataset. Runs differ only in the `design_pattern` field of the overlay.

**Scenarios.**

| Scenario ID | Pattern | Behaviour |
|-------------|---------|-----------|
| `pattern-cot` | Chain-of-Thought | Agent reasons step-by-step before answering |
| `pattern-react` | ReAct | Agent interleaves reasoning and tool calls |
| `pattern-plan-execute` | Plan-and-Execute | Agent builds a plan then executes it |
| `pattern-reflection` | Reflection | Agent critiques and revises its own output |
| `pattern-tree-of-thoughts` | Tree-of-Thoughts | Agent explores multiple reasoning branches |

**Key finding (from paper §5.1).** Plan-and-Execute reaches near-perfect text quality (AR ≈ 0.999) but near-zero task completion (GSR ≈ 0.030). Its answers look plausible and do not solve the task. A single metric hides this gap. You see it only when you measure AR and GSR under identical conditions. ReAct and Reflection sit at the Pareto frontier of quality and latency.

**Metrics.** Answer Relevancy (AR), Goal Success Rate (GSR), session latency (s).

**Output.** `results/figure-01-overhead-quality.png`. It is a scatter plot with 2D confidence intervals. AR and GSR are on the y-axis, latency on the x-axis. Each point is one (pattern, metric) combination.

---

## Exp 1.2 — Coordination topologies on the trip-planner MAS

**What it tests.** Five topology overlays run on the same 4-agent trip-planner MAS and the same 250-item `trip-planner-benchmark` dataset. The agent code is byte-identical across scenarios.

**Scenarios.**

| Scenario ID | Topology | Control flow |
|-------------|----------|-------------|
| `topo-parallel` | Parallel | Moderator fans out to all specialists concurrently |
| `topo-linear-pipeline` | Linear pipeline | Agents execute in a fixed sequential chain |
| `topo-moderator-broker` | Moderator-broker | Dynamic delegation to the right specialist |
| `topo-supervised` | Supervised | Supervisor reviews and approves each agent output |
| `topo-verifier` | Verifier | A dedicated verifier checks all agent outputs |

**Key finding (from paper §5.1).** Whether parallel fan-out beats a moderator-broker baseline depends on the task and dataset. The experiment gives you the means to measure that comparison. It makes no prior claim about which topology wins.

**Metrics.** Goal Success Rate (GSR), session latency (s).

**Output.** `results/figure-02-overhead-quality.png`. The format matches Exp 1.1, but it shows GSR only. Topology scenarios are on the color axis.

---

## Reproducing from scratch

Each experiment is self-contained. Its `experiment.yaml` declares everything a re-run needs: manifest, dataset path, overlays, pipeline. Key files:

```
01-design-patterns/
├── experiment.yaml          # Scenarios, level hooks (run/post), metadata
├── lab-config.yaml          # Lab settings (libraries)
├── pipeline-figure.yaml     # Figure pipeline
└── overlays/                # One YAML per design pattern

02-topologies/
├── experiment.yaml
├── lab-config.yaml
├── pipeline-figure.yaml
└── overlays/                # One YAML per topology
```

Datasets:

- `qa-reasoning-queries` — lab-local complete QA pack (Exp 1.1)
- `trip-planner-benchmark` — complete 250-item trip-planner pack in `library-samples` (`locator: samples`, Exp 1.2)

To run fewer items, set `dataset.limit` on the experiment, or pass `--override 'experiment:experiment.dataset.limit=N'`. Do not keep a reduced copy of the same pack.

The experiment's `application:` block binds the MAS under test:

- Exp 1.1: `manifest: ../apps/qa-agent/agents/qa-agent.yaml` (lab-local app, path relative to the experiment)
- Exp 1.2: `app: trip-planner`, resolved through the library catalog of `mas-library-samples`

For metadata capture and output validation, see the experiment's `metadata:` and `output_schema:` blocks and [Tutorial 3](../../docs/tutorials/03-experiments-and-analysis/README.md).
