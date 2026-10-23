<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Experiments

<p class="eyebrow">The lab control plane</p>

This page is the experiment half of the former architecture page. The five
stages that feed it are in [Architecture](../architecture.md).

`mas-lab benchmark run` first executes all runs, then runs the analysis steps. The
work is organised in four nested levels. Each level can have steps that run when
that level completes:

| Level | YAML key | Scope |
|---|---|---|
| **Experiment** | `post:` | The whole experiment, for example aggregating metrics |
| **Scenario** | `scenario:` | One overlay stack |
| **Item** | `item:` | One dataset item, across all its runs |
| **Run** | `run:` | One execution of one scenario on one item. It writes `traces/events.jsonl` |

`n_runs` repeats each scenario and item pair. Models are not deterministic, so one
run proves nothing. Repeating gives you the **variance**, instead of assuming it.

The experiment's `design` chooses which scenario and item pairs to run:

| Mode | Cells run |
|---|---|
| `cartesian` | Every scenario against every item |
| `coupled` | Only the listed (scenario, items) pairs |
| `one_factor` | Sweep one axis, scenarios or items, while the other is pinned |

Use `one_factor` to vary one thing at a time. Set `design.max_executions` to stop a
plan that is larger than you intended. Set `dataset.limit` for a quick smoke run.

Analysis steps live in these level hooks. An experiment has no separate `pipeline:`
key. You can still run a standalone pipeline file with `mas-lab benchmark pipeline run`.

See the [experiment manifest](../manifests/experiment.md), the
[pipeline manifest](../manifests/pipeline.md),
[Tutorial 3](../tutorials/03-experiments-and-analysis/README.md) and
[Discover → Labs](../discover/labs.md).

## Where the guarantees come from

No single layer gives all the guarantees. They add up across the three.

| | Source | What it covers |
|---|---|---|
| **By design** | The specification and the runtime | Declared roles, allowed tools, policy checks, controlled execution |
| **By validation** | Labs | Building blocks and combinations tested before release |
| **By observation** | Traces and guardrails | What only appears under real workloads, delays and policies |

Four properties build on each other:

1. **Testable**: contracts are fixed points you can write assertions against.
2. **Reproducible**: the configuration is fully resolved before the run, so a run
   depends only on its manifests.
3. **Observable**: events come from the contract boundaries, so nothing depends on
   someone remembering to log.
4. **Governable**: policy is an overlay outside the application, so you can change,
   audit and vary it on its own.

!!! note "The experimental claim"
    A difference you observe is caused by the variable you changed, not by
    something else that varied in the stack.

    This holds only while the variation layers stay separate. That is why the
    rules on [Architecture](../architecture.md) (no model endpoint in a logic or
    control overlay, no API URL in a flavour) are requirements, not style.

Next: [Packages](packages.md) or [Surfaces](surfaces.md).
