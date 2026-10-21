<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Replay equivalence checklist

This page is a checklist for comparing two runs, or two builds. Use it before you claim their traces are **bit-exact** (identical byte for byte) or **behaviourally equivalent** (same events and decisions, values may differ).

Smallest working command: the bit-exactness lab below.

Prerequisite: [docs/cli/observability.md](../../docs/cli/observability.md) —
what `events.jsonl` contains.

## Before comparing

- [ ] Same `experiment.yaml`, overlays, dataset version, and infra ref
- [ ] Same random seeds where the runtime exposes them
- [ ] Observability enabled (`observability-native` overlay or `--events`)
- [ ] llm_cache replay fixture for deterministic CI, or document live-model variance

## Structural equivalence (from traces)

- [ ] Same event kinds and ordering for envelope / tool / LLM boundaries
- [ ] Same governance decisions (`envelope.activity`, policy denials)
- [ ] Same tool names and argument shapes (values may differ under live LLM)

Extract tables: `extract_trace_stats`, `extract_mealy_stats` pipeline steps.

## Bit-exactness lab

This sub-experiment tests bit-exactness: [lifecycle-control.lab/02-bit-exactness](../../labs/lifecycle-control.lab/02-bit-exactness/).

```bash
mas-lab benchmark run labs/lifecycle-control.lab/02-bit-exactness/experiment.yaml --progress
```

## Related

- [Experiments and analysis](../../docs/tutorials/03-experiments-and-analysis/README.md) — caching and reproducibility
- [benchmark-state-architecture.md](benchmark-state-architecture.md)
