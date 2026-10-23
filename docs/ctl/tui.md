<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-ctl` terminal UI (TUI)

`mas-ctl tui` is a full-screen terminal chat. It starts like `mas-ctl chat`: it
merges the manifest and its overlays, resolves infra refs, and enables
human-in-the-loop (HITL) approval and observability. Terms: [glossary.md](../glossary.md).

Use the TUI for long interactive sessions. For other jobs:

- Scripts and the `--trace` exchange log: `mas-ctl chat`.
- Past benchmark runs: the [web UI](../ui/index.md).
- Experiments with level hooks: `mas-lab benchmark run`.

You need an agent or MAS manifest first
([Tutorial 1](../tutorials/01-building-an-agent/README.md)).

## Basic usage

```bash
mas-ctl tui agent.yaml
mas-ctl tui agent.yaml -o overlays/governance-hitl.yaml   # any overlay you have; here a HITL one
mas-ctl tui agent.yaml -o docs/schemas/examples/overlays/observability-native.yaml
```

## Flags (parity with `mas-ctl chat`)

| Flag | Description |
|------|-------------|
| `-o / --overlay` | **Overlay** YAML (repeatable) |
| `--override PATH=VALUE` | Schema-backed patch, applied last ([overrides](../cli/overrides.md)) |
| `--bind NAME=URI` | Shortcut for an A2A / MCP `usage: use` infra row |
| `--pattern` | Design-pattern plugin id |
| `--flavour` | Flavour (default `local`) |
| `--infra-ref` / `--infra` | Infra bundle (`standard:openai`, …); repeatable |
| `--runtime-ref` | RuntimeEngine manifest ref; repeatable |
| `--model ID` · `--max-tokens N` | Force the model / output-token cap for this run |
| `--memory-seed` | Seed file for memory plugins |
| `--single-turn` | Exit after one user turn |
| `--no-validate` | Skip manifest validation |

### Observability

| Flag | Description |
|------|-------------|
| `--events` / `--no-events` | Override manifest **observability** |
| `--events-file PATH` | **`events.jsonl`** output path |
| `--events-stderr` | Also stream JSONL on stderr (`--events-stdout` is a legacy alias) |
| `--events-format` | `native` · `boundary` · `both` · `otel` |

Full reference: [cli/observability.md](../cli/observability.md) ·
[mas-ctl flags](../cli/mas-ctl.md).

`--trace` (human exchange log on stderr) is on **`chat`** / **`run-mas`**:

```bash
mas-ctl chat agent.yaml -i --trace --events --events-file traces/events.jsonl
mas-ctl tui agent.yaml --events --events-file traces/events.jsonl
```

## HITL

Governance overlays that require human approval work in the TUI as they do in chat.

## When to use what

| Tool | Best for |
|------|----------|
| `mas-ctl chat` | Scripts, **exchange log**, CI smoke |
| `mas-ctl tui` | Interactive terminal, HITL |
| Web UI | **Run** history, plots, demos |
| `mas-lab benchmark run` | **Scenarios**, **dataset**, level hooks |

## Related

- [cli/index.md](../cli/index.md) — command-line index
- [cli/mas-ctl.md](../cli/mas-ctl.md) — flags (`tui` has `--events*`, not `--trace`)
- [cli/observability.md](../cli/observability.md)
- [Web UI](../ui/index.md) · [Live dashboard](../ui/live.md) · [Controller API](../cli/serve.md)
- [Tutorial 1](../tutorials/01-building-an-agent/README.md)

Next: [Tutorial 14 — Governance and HITL](../tutorials/14-governance-hitl/README.md).
