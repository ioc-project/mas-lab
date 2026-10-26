<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->

# Labs

<p class="eyebrow">The experiments behind the IoC results, and the paper labs</p>

A **lab** is a packaged experiment. Like a good lab notebook, it holds everything
needed to repeat the work: the systems under test, the changes to try, the questions
to ask, and the analysis that turns runs into figures and numbers. Anyone can run a
lab again and check the result.

This page covers every lab: the IoC labs, which are published outside this
repository, and the [paper labs](#paper-labs) that ship in it, with their run
commands, figures, and reproduction status.

The IoC labs form a chain of evidence. Each one answers a question the next one
builds on.

| Question | Lab | Where to read it |
|---|---|---|
| **How do multi-agent systems fail?** | [IoC motivation lab](#ioc-motivation-lab) | This page |
| **Do ACCORD and CONCORD repair those failures, and at what cost?** | [Cognition protocols](#cognition-protocols) | This page |
| **Do the failures show up on other systems too?** | [Cognitive-challenge labs](#cognitive-challenge-labs) | This page |
| **Is the method itself sound?** | [Paper labs](#paper-labs) | This page |

## How every lab works

Every lab is an A/B test, as described in
[Four ideas to know](../index.md#four-ideas-to-know). It follows the same four steps.

<div class="grid cards" markdown>

-   **1 · Change one thing**

    ---

    An overlay either breaks something on purpose or adds a protocol. The system's
    own description is not touched.

-   **2 · Run, many times**

    ---

    The unchanged system and each changed version answer the same questions,
    several times each.

-   **3 · Observe**

    ---

    Every run is scored. Runs can also be sent to [SCOPE](https://ioc-project.github.io/scope/),
    as shown in [Integrations](integrations.md#runs-come-out-for-scope).

-   **4 · Compare**

    ---

    Each version is compared with the unchanged system. Only the overlay differs,
    so the gap is the overlay's effect.

</div>

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
[labs vs libraries](../labs-and-libraries.md) and the
[experiment manifest](../manifests/experiment.md).

## IoC motivation lab

**The question.** What goes wrong in a working multi-agent system, and how badly?

**The set-up.** This lab accompanies the study
[Cognitive challenges in multi-agent systems](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems).
It takes an incident-investigation team that works well and introduces nine kinds of
cognitive failure into it, one at a time, each through its own overlay. Some
examples:

- agents that pursue different goals without saying so;
- agents that mean different things by the same word;
- agents that accept a claim without asking for evidence.

The team runs in two layouts: one agent in charge of the others, and an open debate
among equals.

**What is measured.** Each failure is compared with the healthy team on the same
incidents. A heatmap marks a failure as harmful when a score drops by 25 percent or
more.

**Repeat it.** The lab is published as the
[ioc-motivation-labs dataset](https://huggingface.co/datasets/ioc-project/ioc-motivation-labs) on Hugging Face, the companion of the
[blog post](https://outshift.cisco.com/blog/ai-ml/cognitive-challenges-in-multi-agent-systems). Its recorded runs and scores let the figures
be rebuilt without calling a language model at all. The lab documentation explains
each failure, how to record the runs again from scratch, and how to browse them in
the MAS-Lab web interface.

## Cognition protocols

**The question.** When ACCORD and CONCORD are added to the agents, how many failures
do they repair, and what do they cost?

**The set-up.** A lead agent and five specialists must agree on one fix for an
incident. Each incident has a known right answer. The team as shipped uses no
protocol, which makes it a fair baseline. The protocols are added as skills by
overlays (see
[Integrations](integrations.md#cognition-protocols-go-in-as-skills)). The main
experiment runs five versions of the team, five times each, with a live language
model:

| Version | What changes |
|---|---|
| **Baseline** | Nothing |
| **CONCORD** | The lead gets the initiator skill, every specialist the receiver skill |
| **CONCORD, always on** | As above, but the lead is told to start CONCORD every time. This tests the whole protocol without depending on the model deciding to use it |
| **ACCORD** | The ACCORD skills, in the same way |
| **ACCORD and CONCORD** | Both protocols together |

A second experiment focuses on incidents where the specialists genuinely disagree,
and compares the team with and without CONCORD.

**What is measured.** Two scores, and both are needed:

| Score | Question it answers |
|---|---|
| **Certification** | Did every agent rate the final decision at or above its own minimum? |
| **Correctness** | Was the decision the right one? |

CONCORD certifies that every agent found the decision acceptable. That is not the
same as the decision being right: a team can agree, confidently, on the wrong fix.
Looking at certification alone would count that as a success, which is why
correctness is always reported next to it. The recorded runs also give the cost of
the protocol, the number of rounds it took, and how well the least satisfied agent
fared. These are computed afterwards, from the records, with no further model calls.

**Repeat it.** The labs and their recorded runs are published as the
[ioc-eval-labs dataset](https://huggingface.co/datasets/ioc-project/ioc-eval-labs) on Hugging Face.

**Results.**
[Cognition Protocol evaluation](https://ioc-project.github.io/cognition-protocols/evaluation/) ·
[ACCORD evaluation](https://ioc-project.github.io/cognition-protocols/evaluation/accord/) ·
[CONCORD evaluation](https://ioc-project.github.io/cognition-protocols/evaluation/concord/)

### A small worked example with four agents

A second set-up is small enough to follow by hand. Four agents each look after one
part of an infrastructure (a data centre, the network, the telemetry and a
database) and can see only their own part. They share nothing but a score between
0 and 1 and one sentence of reasoning, so no agent can check what another one
claims.

The example compares CONCORD with six simpler ways of deciding: letting the incident
owner choose, taking a vote, asking a language model to pick an option, asking it to
write a plan, and so on. The strongest of these, a language model writing a new
plan, produces a plan that suits every agent but one. In the recorded run, only
CONCORD checks that the plan suits every agent.

A recorded run can be replayed instantly, with the same result every time and no
language model, which makes it a good way to see the protocol at work. It is the
source of the worked example in the
[CONCORD specification](https://ioc-project.github.io/cognition-protocols/protocols/concord/specification/).

## Cognitive-challenge labs

**The question.** Are the failures specific to one team, or do they appear on other
systems as well?

**The set-up.** IoC's reference applications include a wider set of failure
experiments on two systems: the incident team, and a travel planner made of four
agents. The kinds of failure are introduced in
[Motivation](https://ioc-project.github.io/overview/motivation/).

A failure can be introduced in two ways:

| Approach | What changes |
|---|---|
| **Overlay injection** | The agents' instructions are altered so that they fail in a given way |
| **Prompt sweep** | The question is rewritten, again and again, until a separate judge model confirms that the failure occurred |

Both approaches leave the same kind of record, so one scoring method covers both, and
one command scores a run.

## Paper labs

MAS Lab ships **three reproducible labs**. Each one reproduces an experiment
section of the paper *MAS-Lab: A Specification-Driven Validation Framework for
Reliable Multi-Agent Systems* (see the [paper page](../paper/index.md)). The
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

### Running them

The labs run over the sample applications shipped with MAS Lab. Run them from a
checkout of the repository, after
[Tutorial 0](../tutorials/00-environment-setup/README.md) has set up the install and a
model endpoint.

```bash
task reproduce                                   # all four experiments
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --dry-run
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml \
  --override 'experiment:experiment.dataset.limit=5' \
  --max-runs 1 --progress                        # quick smoke: five items, one run each
mas-lab benchmark show last plots
```

Without [go-task](https://taskfile.dev/), run the four experiments one by one:

```bash
mas-lab benchmark run labs/design-space.lab/01-design-patterns/experiment.yaml --progress
mas-lab benchmark run labs/design-space.lab/02-topologies/experiment.yaml --progress
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress
mas-lab benchmark run labs/extensions.lab/experiment.yaml --progress
```

To keep a smaller run, set `dataset.limit` in the experiment. Do not keep a trimmed
copy of the dataset. Finished runs are cached, so a rerun refreshes the figures
without new model calls.

See the [paper page](../paper/index.md), the [CLI overrides](../cli/overrides.md) and
[User configuration](../user-config.md) for output locations.

## Labs from any folder

A lab does not have to live in this repository, for example one downloaded from
Hugging Face. Put the folder anywhere, then use one of these:

- **Run it from its path.**

    ```bash
    mas-lab benchmark run /path/to/<name>.lab/experiment.yaml --progress
    ```

- **Have the web UI discover it.** Add its folder, or the folder that contains
  several labs, to `config.yaml`. Relative and absolute paths both work, and a path
  may itself be a `*.lab` folder:

    ```yaml
    mas_lab:
      labs_search_paths:
        - /path/to/<name>.lab
    ```

- **Pin the UI to it.**

    ```bash
    mas-lab serve --workspace /path/to/<name>.lab
    ```

See [Web UI](../ui/index.md) and [`mas-lab serve`](../cli/serve.md) for the catalog
options.

## Writing your own lab

A lab folder holds a `lab-config.yaml`, an `experiment.yaml`, datasets, overlays
and, if needed, custom analysis steps. Keep it flat. Put reusable applications and
plugins in a *library* next to it, and refer to them as `name:path`.

1. Build the agent and the MAS you want to study:
   [Tutorial 1](../tutorials/01-building-an-agent/README.md),
   [Tutorial 2](../tutorials/02-creating-a-mas/README.md).
2. Write one overlay per scenario. Each overlay changes one factor.
3. Declare the scenarios, the dataset, `n_runs` and the analysis steps:
   [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md).
4. Choose or write the metrics:
   [Tutorial 10](../tutorials/10-evaluation-metrics/README.md).

See [labs vs libraries](../labs-and-libraries.md), the
[dataset manifest](../manifests/dataset.md) and the
[pipeline manifest](../manifests/pipeline.md).
