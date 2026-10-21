<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Flavour manifest (`kind: Flavour`)

**Package:** `mas-runtime` | **Schema:** [`flavour.schema.yaml`](../schemas/runtime/flavour.schema.yaml)

A **flavour** (`kind: Flavour`) is a deployment preset.
It declares how a MAS exports **observability** data (traces and metrics), which agent-to-agent protocol it uses, and which tools it exposes.
Write one when the same application must run with different protocol, telemetry, or tool policy, for example local versus MCP exposure.
MCP (Model Context Protocol) is a standard for serving tools to agents over a connection.

A flavour never holds model API URLs.
Those live in **infra** manifests (the bundles that say where endpoints are; see [infra.md](infra.md)) and in agent `models`.

This is the shipped `mcp` flavour:

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata:
  name: mcp
  description: Expose local tools through the MCP protocol.
spec:
  tools:
    exposure_protocol: mcp
  observability:
    - native
```

Shipped flavours use `apiVersion: flavour/v1`.
The schema validates `apiVersion` as a string and does not constrain it to a constant.
A flavour sits between application manifests and infrastructure.
RAG and skills backends are agent concerns.

Terms are in the [glossary](../glossary.md).
Back to the [manifest hub](README.md).

## Two aspects

| Aspect | Flavour sections | Examples |
|--------|------------------|----------|
| **Control / observation** | `observability`, `telemetry`, `tools.exposure_protocol` | Native/OTel export; MCP exposure protocol |
| **Protocol / comm** | `agent_comm` | `protocol` (shipped: `agent-local`), `mode`, `emulation` |

Governance plugins that alter the agent's trajectory are declared in Agent `governance[]`, directly or through an **overlay**.
Control-plane plugins use `control`.
The flavour selects the deployment posture and the plugin defaults.

To mimic another product (for example an OpenClaw-like profile), compose overlays instead of copying every plugin into the flavour YAML.
`with-hardened` adds an undeclared-tool BLOCK plus a typed retry and circuit breaker.
Add observability overlays next, and later checkpoint and backtrack.
See [governance.md](governance.md) and [reliability.md](../references/reliability.md#composing-a-flavour-openclaw-style).

## Schema fields

The schema keeps `spec` open (`additionalProperties: true`).
Its declared sections are:

| `spec` field | Purpose |
|--------------|---------|
| `agent_comm` | Agent-to-agent protocol, mode, emulation, and addresses. The shipped flavours use `protocol: agent-local`; the schema types `protocol` as a free string. |
| `capabilities` | Feature flags and capability overrides. |
| `telemetry` | Basic telemetry sink configuration. |
| `observability` | Observability plugin selection. |
| `control` | Control-plane plugin selection. |
| `tools` | Tool exposure protocol, remote-tool policy, and allow-list. |
| `config` | Free-form deployment configuration. Never store secret values. |
| `operator` | Operator/provisioner metadata. |

## What does not belong here

`FlavourSeparationValidator` (`mas.ctl.validate.separation`) enforces this table.
The bench manifest validator (`mas.lab.mas_validator`) runs it over every `flavours/*.yaml`.

| Forbidden in Flavour | Belongs in |
|---------------------|------------|
| `spec.llm` (model, `api_base`) | Agent `models` + infra `LLMProxy` |
| `spec.skills` | Agent `skills` |
| `spec.prefer_local` | Workspace infra refs / runtime tuning |
| `spec.infra_refs`, `spec.infra_ref` | Workspace `config.yaml`, CLI `--infra-ref` |

## Selection at run time

Flavours select how the application exposes agents or tools.
They do not select MCP consumption.
A `ToolServerRegistry` with `usage: use` is an infra dependency, and `--bind NAME=mcp://...` is a shortcut for that infra row.
`--flavour mcp` selects the tool *exposure* protocol.
Internal agency stays in-process on flavour `local` until an Application endpoint sets `usage: use`.
Set that endpoint with `--infra-ref` YAML or `--bind NAME=a2a://...`.

```bash
mas-ctl run-mas mas.yaml --infra-ref ./infra/prod-bundle.yaml
```

For benchmarks, select a flavour on the experiment or the CLI:

```bash
mas-lab benchmark run experiment.yaml --flavour local
```

`metadata.default_flavour` on a MAS provides the default name.

In OSS mas-lab, the canonical flavour YAML lives only under `library-standard/src/mas/library/standard/flavours/`.
It contains `local.yaml`, `mcp.yaml`, and `local-benchmark.yaml`.
The scheduler resolves flavours with `mas.lab.flavour.resolve.resolve_flavour_path()`.
Do not copy flavour YAML into individual labs or samples.

## See also

- [infra.md](infra.md): where model and tool endpoints live
- [experiment.md](experiment.md): `default_flavour`
- [mas.md](mas.md): `metadata.default_flavour`
- [Tutorial 3](../tutorials/03-experiments-and-analysis/README.md): benchmarks that select a flavour
