<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lifecycle control lab (paper §5.2)

This lab stacks governance **overlays** (budget, guardrail, circuit breaker, policies) on the trip-planner **MAS**. It measures what each layer costs and what it catches. It reads `events.jsonl` traces only and uses no LLM judge. Read this page to run the paper §5.2 experiment.

Terms: [glossary.md](../../docs/glossary.md).

## Run

Smallest working command, run from the repo root. You should see a progress bar, then figures under the output path below.

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --progress

# Validate the configuration without calling a model
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --dry-run

# Topology bit-exactness sub-experiment (3 replicates per scenario and item)
mas-lab benchmark run labs/lifecycle-control.lab/02-bit-exactness/experiment.yaml --progress
```

`experiment-whatif-smoke.yaml` is a two-scenario variant (baseline and guardrail). Run it with a shared LLM response cache. Then any trajectory difference comes from governance alone. See [LLM cache](../../docs/manifests/llm-cache.md) to record and replay model responses.

## Output

`experiment.name` is `lifecycle-control`. Results go under `$XDG_DATA_HOME/mas/labs/lifecycle-control/lifecycle-control/`. `mas-lab config` prints the effective data root.

```bash
mas-lab benchmark show last plots
```

## Claim

Governance controls are orthogonal layers added by overlay. They do not interfere with nominal operation. They fire when, and only when, a fault is injected.

## Layout

| Path | Role |
|------|------|
| `experiment.yaml` | Nine **scenarios** (Exp 2.1 control stacking, Exp 2.3 unified policy), `lifecycle-queries` **dataset** (13 items), level hooks that build the figures |
| `02-bit-exactness/` | Sub-experiment with its own `experiment.yaml` and overlays |
| `overlays/` | Governance **overlay** manifests, one per scenario |
| `datasets/` | Lab-local datasets |
| `lifecycle_control_lab/` | Lab-local library with the custom figure **pipeline steps** |
| `RESULTS.md` | Status of what the lab runs today |

## See also

- [labs-quickstart.md](../../lab/docs/labs-quickstart.md)
- [paper/index.md](../../docs/paper/index.md)
- [governance manifests](../../docs/manifests/governance.md)
