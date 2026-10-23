<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Lifecycle Control Lab — Results

## OSS status (mas-lab)

**Status note (2026-10-19):** this file records what the lab runs. It holds no measured results. Run the experiments to produce the figures (see [README](README.md)). The scenarios below are those in `experiment.yaml` at v0.2.0.

The main experiment [`experiment.yaml`](experiment.yaml) runs with a mock or live LLM. It covers:

- Exp 2.1: governance stacking (baseline, budget, guardrail, production).
- Exp 2.3: unified governance policy model.
- Pipeline figures: per-call overhead, policy density, fault detection heatmap.

```bash
mas-lab benchmark run labs/lifecycle-control.lab/experiment.yaml --dry-run
```

Sub-experiment [`02-bit-exactness/`](02-bit-exactness/) reproduces topology bit-exactness (identical traces across replicates). It uses lab-local overlays and no cross-lab symlinks.

Observability: the overlays use only the `native` observability plugin, which writes `events.jsonl`. This lab has no OpenTelemetry variants or knowledge-graph span pipelines. See [Tutorial 8](../../docs/tutorials/08-telemetry/README.md) and [Tutorial 9](../../docs/tutorials/09-kg-scope/README.md).

---

## Bit-exactness (02-bit-exactness)

The earlier analysis exists only in this file's git history. To regenerate the bit-exactness figures, run:

```bash
mas-lab benchmark run labs/lifecycle-control.lab/02-bit-exactness/experiment.yaml
```
