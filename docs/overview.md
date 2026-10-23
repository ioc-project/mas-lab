<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Overview

This page explains the problem MAS Lab addresses, how it is built and who it is for.
For a short introduction to the toolkit, see [What is MAS-Lab](what-is/index.md).
For MAS-Lab's place in the Internet of Cognition, see the [landing page](index.md).

## The problem

Agent logic, orchestration, monitoring, and control are usually mixed together in
prompts and ad hoc code. The system is then hard to test, to reproduce, and to
predict outside the demo path. What a team learns from experiments says little
about production, where budgets are tighter and policies are stricter.

??? example "A trip planner: what goes wrong between demo and production"

    A coordinator receives a request and splits it into subtasks. It delegates
    them to agents that fetch schedules, compute routes, and estimate cost. On a
    few queries the demo works.

    To prepare for production, the team adds memory for user preferences, a
    tighter reasoning pattern, and a budget cap. Each change is small. Together
    they break things: the same query returns different results, handoffs become
    inconsistent, and latency and cost drift. Undoing a change does not restore the
    old behaviour.

    There is nowhere to start debugging, because the system has no single
    definition to inspect.

Multi-agent workflows need **explicit intent, controlled execution, and evidence
that carries from development to production**.

## Three building blocks

MAS Lab is not another agent framework. It is a layer around your agents.

| Block | What it is | The question it answers |
| --- | --- | --- |
| **Specification** | A versioned definition of agents, roles, tools, delegation, and policies | *What is this workflow, and which version are we testing?* |
| **Runtime** | Execution that enforces the definition and records what happens | *How is the definition enforced while the system runs, including failures?* |
| **Labs** | Repeatable experiments that change one factor at a time | *What changes when we vary one design choice or policy?* |

Keeping the three apart stops a small change from spreading in unpredictable ways.
See the [architecture](architecture.md) and the [labs](labs.md).

### Why the specification comes first

- It gives the workflow an **identity** you can version, compare, and cite.
- It makes monitoring meaningful: traces refer to named roles, tools, and
  delegation paths, not to an anonymous prompt.
- It makes analysis possible: an experiment needs a declared baseline.

In practice the specification is a set of YAML files, called **manifests**. An
[agent manifest](manifests/agent.md) describes one agent. A
[MAS manifest](manifests/mas.md) describes a team. An
[overlay](manifests/overlay.md) is a small file that changes either one without
copying it. You can infer a specification from an existing system to get started.
The goal is still an explicit one, written first.

## Silent failures

Some failures raise no error: a delegation to the wrong agent that still returns an
answer, stale memory that sounds plausible, coordination that drifts only under
load. You cannot rule them out when you write the specification, and the runtime
does not flag them.

To diagnose one, compare the *intended* behaviour (the specification) with the
*actual* behaviour (the trace). MAS Lab keeps both under one identity. A failure
then points to the exact workflow version, policy, and interaction that caused it.
The fix goes back into the specification, the policies, or the experiments.

## One specification across the lifecycle

Teams often rebuild the workflow between development, test, and production. A test
on a toy setup then says little about the real deployment.

MAS Lab uses one specification throughout. Developers edit it. Labs benchmark
variants of it. Operations deploy and monitor it. A failed lab run or a drifted
trace points back to the definition reviewers saw.

## Who it is for

- **Developers** build modular agent apps from specs. Declare agents, tools, and
  workflows once, then try other models and coordination patterns. You can diff,
  branch, and benchmark a specification. First step:
  [Tutorial 0](tutorials/00-environment-setup/README.md).
- **Enterprises** add governance, traceability, and budgets as overlays. The
  specification states which agents exist, what they can access, and which policies
  apply. The runtime enforces it and writes an audit trail. First step:
  [Tutorial 14](tutorials/14-governance-hitl/README.md) and the
  [user guide](user-guide.md).
- **Researchers** run controlled multi-run experiments. The specification names the
  variable you change, and a lab holds everything else fixed. Reproduce the article
  with the [paper labs](paper/index.md).

## How it fits the ecosystem

Teams already use agent frameworks, protocols such as MCP and A2A, and monitoring
or evaluation platforms. These tools help, but they are usually assembled around
workflows that have **no shared definition**.

MAS Lab does not replace them. It gives them one versioned specification to attach
to, whatever the agents or framework. Monitoring platforms provide traces.
Governance layers enforce policy. Evaluation tools produce comparisons.

Remote tools (MCP) and remote agents (A2A) are deployment details, not part of the
agent's logic. You swap in an adapter and the agent stays the same. See
[Tutorial 4](tutorials/04-mcp-tools/README.md) and
[Tutorial 5](tutorials/05-a2a-agents/README.md). You do not need a clean slate:
you can import or infer an existing system.

Next: [Architecture](architecture.md) or [Discover](discover/index.md).
