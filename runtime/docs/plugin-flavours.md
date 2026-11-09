<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Flavours: linking agents to infra

A flavour is a small YAML document (`kind: Flavour`) that links the logic spec to the infra, so an agent definition does not change between a laptop, a benchmark run, and a deployment.
Its `bind` section says which infra rows each agent rides and what this deployment does with it ([docs/manifests/flavour.md](../../docs/manifests/flavour.md#bind)); it defines nothing itself.
This page is for authors who run agents in more than one environment.

Leftover Flavour keys (`agent_comm`, `tools`, `control`, `telemetry`) are accepted and read by nothing. `observability` is deprecated and still the fallback when the agent declares none.
A flavour holds no model endpoints or credentials. Those live in the agent's `models` and in **infra** manifests (see [docs/manifests/infra.md](../../docs/manifests/infra.md)).

Select a flavour when you run an agent:

```bash
mas-ctl chat agent.yaml --flavour local
```

## Shipped flavours

`library-standard` ships two flavours (`library-standard/src/mas/library/standard/flavours/`):

| Flavour | Purpose |
|---------|---------|
| `local` | Default development flavour. In-process communication is the library default, not this file's leftover `agent_comm` block. `observability: [native]` is still the fallback. |
| `local-benchmark` | Same leftover keys as `local`, for `mas-lab` batch runs |

Select one with `--flavour NAME` (`local`) or `--flavour PATH`.

## Anatomy

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata:
  name: local
spec:
  agent_comm:
    protocol: agent-local     # deprecated, read by nothing
    mode: local
    emulation: true
  tools:
    remote_tools_enabled: false
    allowed: ["*"]
  telemetry:
    backend: file
    path: logs/
  observability:
    - native                  # deprecated fallback: used when the agent declares no observability
```

The full list of sections (`bind`, leftover keys, `capabilities`, `config`, `operator`)
and what must not appear in a flavour is in
[docs/manifests/flavour.md](../../docs/manifests/flavour.md).

## Selecting a flavour

```bash
mas-ctl chat agent.yaml --flavour local
mas-ctl run-mas mas.yaml --flavour local
mas-lab benchmark run experiment.yaml --flavour local-benchmark
```

Without `--flavour`, `mas-ctl` uses `local`. It accepts the bundled `local`, or the path of a Flavour YAML file (a value ending in `.yaml` / `.yml` or holding a path separator).

Infra (endpoints of LLM providers, tool servers, remote agents) is selected separately, with `--infra-ref` or `--connect`. One flavour then works against several environments:

```bash
mas-ctl run-mas mas.yaml --flavour local --infra-ref ./infra/prod-bundle.yaml
```

## Observability as a deployment concern

Agents contain no telemetry code. Flavour `observability` is deprecated and still the fallback when the agent declares none; `--events/--no-events`, `--events-format` and `--events-file` still patch that key. See [docs/cli/observability.md](../../docs/cli/observability.md).

## Creating a flavour

1. Copy a shipped flavour into your library's `flavours/` directory and rename `metadata.name`.
2. Change only deployment concerns. The validator rejects `spec.llm`, `spec.skills`, and `spec.infra_refs`.
3. Run it with `--flavour PATH`.

## See also

- [docs/manifests/flavour.md](../../docs/manifests/flavour.md): schema reference
- [agent-defaults.md](agent-defaults.md): package-wide defaults for model, design pattern and context manager
- [README.md](../README.md): `mas-runtime` overview
