<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Flavours: environment-based configuration

A flavour is a small YAML document (`kind: Flavour`) that sets a deployment posture and plugin defaults, so an agent definition does not change between a laptop, a benchmark run, and a deployment.
This page is for authors who run agents in more than one environment.

Environment-dependent choices (how observability is exported, which transport agents use, whether remote tools are allowed) belong in a flavour, not in the agent or MAS manifest.
A flavour holds no model endpoints or credentials. Those live in the agent's `models` and in **infra** manifests (see [docs/manifests/infra.md](../../docs/manifests/infra.md)).

Select a flavour when you run an agent:

```bash
mas-ctl chat agent.yaml --flavour local
```

## Shipped flavours

`mas-library-standard` ships three flavours (`library-standard/src/mas/library/standard/flavours/`):

| Flavour | Purpose |
|---------|---------|
| `local` | Default development flavour: in-process agent communication, native observability, file telemetry under `logs/`, remote tools disabled |
| `local-benchmark` | Same posture for `mas-lab` batch runs |
| `mcp` | Expose the agent's local tools through the MCP protocol (`tools.exposure_protocol: mcp`) |

List what is installed with:

```bash
mas-ctl flavour list
```

## Anatomy

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata:
  name: local
spec:
  agent_comm:
    protocol: agent-local     # in-process communication
    mode: local
    emulation: true
  tools:
    remote_tools_enabled: false
    allowed: ["*"]
  telemetry:
    backend: file
    path: logs/
  observability:
    - native                  # plugin ids; selects the events.jsonl recorder
```

The full list of sections (`agent_comm`, `capabilities`, `telemetry`, `observability`, `control`, `tools`,
`config`, `operator`) and what must not appear in a flavour is in
[docs/manifests/flavour.md](../../docs/manifests/flavour.md).

## Selecting a flavour

```bash
mas-ctl chat agent.yaml --flavour local
mas-ctl run-mas mas.yaml --flavour local
mas-lab benchmark run experiment.yaml --flavour local-benchmark
```

Without `--flavour`, the runtime uses `metadata.default_flavour` of the MAS manifest (default `local`). In this release `mas-ctl run-mas` and `mas-ctl tui` accept only `local`; `mas-ctl chat` accepts `local` and `mcp`.

Infra (endpoints of LLM providers, tool servers, remote agents) is selected separately, with `--infra-ref` or `--connect`. One flavour then works against several environments:

```bash
mas-ctl run-mas mas.yaml --flavour local --infra-ref ./infra/prod-bundle.yaml
```

## Observability as a deployment concern

Agents contain no telemetry code. The flavour's `observability` list and the manifest or CLI overrides (`--events/--no-events`, `--events-format`, `--events-file`) decide whether and how events are recorded. See [docs/cli/observability.md](../../docs/cli/observability.md).

## Creating a flavour

1. Copy a shipped flavour into your library's `flavours/` directory and rename `metadata.name`.
2. Change only deployment concerns. The validator rejects `spec.llm`, `spec.skills`, and `spec.infra_refs`.
3. Confirm it is discovered with `mas-ctl flavour list`.

## See also

- [docs/manifests/flavour.md](../../docs/manifests/flavour.md): schema reference
- [agent-defaults.md](agent-defaults.md): package-wide defaults for model, design pattern and context manager
- [README.md](../README.md): `mas-runtime` overview
