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

This page is the index. The left menu also lists the [paper labs](../labs.md)
shipped in this repository — those pages go into run commands, figures, and
reproduction status.

The IoC labs form a chain of evidence. Each one answers a question the next one
builds on.

| Question | Lab | Where to read it |
|---|---|---|
| **How do multi-agent systems fail?** | [IoC motivation lab](#ioc-motivation-lab) | This page, and the Outshift study it accompanies |
| **Do ACCORD and CONCORD repair those failures, and at what cost?** | [L9 evaluation labs](#l9-evaluation-labs) | This page, and the [Cognition Protocol benchmarks](https://ioc-project.github.io/cognition-protocols/benchmarks/) |
| **Do the failures show up on other systems too?** | [Cognitive-challenge labs](#cognitive-challenge-labs) | This page |
| **Is the method itself sound?** | [Paper labs](#paper-labs) | [Toolkit labs page](../labs.md) |

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

**Repeat it.** The recorded runs and scores are published with the lab, so the figures
can be rebuilt without calling a language model at all. The lab documentation explains
each failure, how to record the runs again from scratch, and how to browse them in
the MAS-Lab web interface.

## L9 evaluation labs

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

**Results.**
[Cognition Protocol benchmarks](https://ioc-project.github.io/cognition-protocols/benchmarks/) ·
[ACCORD evaluation](https://ioc-project.github.io/cognition-protocols/accord/evaluation/) ·
[CONCORD evaluation](https://ioc-project.github.io/cognition-protocols/concord/evaluation/)

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
[CONCORD design](https://ioc-project.github.io/cognition-protocols/concord/design/).

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

**The question.** Does MAS-Lab measure what it claims to measure?

**The set-up.** These labs reproduce the experiments of the MAS-Lab paper. They test
the toolkit itself on example systems and need no IoC component. They ship in this
repository.

| Lab | What it varies |
|---|---|
| [Design space](https://github.com/ioc-project/mas-lab/tree/main/labs/design-space.lab) | How agents reason, and how the team is laid out |
| [Lifecycle control](https://github.com/ioc-project/mas-lab/tree/main/labs/lifecycle-control.lab) | Rules and limits applied to agents, and what they cost |
| [Extensions](https://github.com/ioc-project/mas-lab/tree/main/labs/extensions.lab) | Adding memory to a system |

The [toolkit labs page](../labs.md) explains how to run them, what each figure
shows, and the reproduction status.
