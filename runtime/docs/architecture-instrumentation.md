<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Architecture & Instrumentation Guide

This page explains how `mas-runtime` relates to agent frameworks, where governance and observability attach, and what "instrumentation" means in this release. It is for developers who want to run an existing framework application under the kernel.

## 1. Kernel, not a framework wrapper

`mas-runtime` owns the agent loop. It does not patch or subclass LangChain, LangGraph, AutoGen or
LlamaIndex objects. Instead the loop is an explicit Mealy machine (`RuntimeKernel`): ingress symbols go in,
egress symbols come out, and a driver (`KernelDriver`) executes the side effects (model call, tool call,
human question, context assembly) through an **engine** (`EngineContract.invoke`) and feeds the result back
as an ingress symbol. See [production-path.md](production-path.md) and [mealy-envelope.md](mealy-envelope.md).

Because every crossing of the loop passes through the same envelope, governance and observability apply
uniformly, whatever produces the model or tool results behind the engine:

```text
user text -> RuntimeKernel -> egress symbol -> [governance chain] -> engine (LLM / tool / ...)
                  ^                                                      |
                  +------------------ ingress symbol <-------------------+  (validated, recorded)
```

## 2. Framework adapters

The MAS manifest selects a framework adapter with `spec.framework.default_adapter`
(see [docs/manifests/mas.md](../../docs/manifests/mas.md#framework)).

| Adapter | Status |
|---------|--------|
| `native` | Supported: the Mealy kernel runs the agents directly |
| `langgraph` | Experimental wrapper in `mas.ctl.compose.adapters.langgraph`; it embeds a `RuntimeInstance` in a LangGraph `StateGraph` and needs the `langgraph` package. Not a supported path in this release |
| `autogen`, `crewai` | Reserved ids, not shipped |

To run an existing LangChain, LangGraph or AutoGen application under governance, the supported pattern is to
implement an engine (or a tool provider) that calls into that application and to let the kernel drive it; the
application's own loop then becomes the body of one engine call. No ready-made wrappers for those frameworks
ship in this repository.

## 3. Tools

Tools are `ToolContract` implementations behind a `kind: Tool` manifest, or remote tool servers bound
through an infra `ToolServerRegistry` (for example `--connect tool=mcp://host:port/mcp#name` on `mas-ctl`). Both
paths are governed identically: every tool call is an egress that passes the governance chain, and its result
is an ingress that is validated and recorded. See [dev/schemas/tool-definition.md](dev/schemas/tool-definition.md),
[docs/manifests/infra.md](../../docs/manifests/infra.md) and Tutorial 4 (MCP tools).

## 4. Observability

- Native recording: every envelope crossing is written to `events.jsonl`
  (see [trajectory-schema.md](trajectory-schema.md) and [plugin-and-tool-authoring.md](plugin-and-tool-authoring.md#6-observability)).
- OpenTelemetry: native events can be converted to OTLP spans (`mas-lab otel`, `mas-lab telemetry`) or
  exported live through the manifest `observability` configuration.
- Per-turn console trace: `mas-ctl chat --trace` (and `--trace-full`, `--trace-summary`).

## 5. Why the Mealy machine is explicit

The kernel transition function is a pure function of the product state and the ingress symbol, which makes
governance decisions replayable, traces checkable against the machine, and checkpoints (snapshot and resume of
the product state) possible. The formal treatment is in [automaton-product-model.md](automaton-product-model.md)
and [mealy-machines-guide.md](mealy-machines-guide.md).

## Summary

| Choice | Rationale |
|--------|-----------|
| Kernel owns the loop | Governance and observability cannot be bypassed by a framework |
| Engine boundary | LLM providers, tool servers and framework code sit behind one interface |
| Explicit Mealy machine | Replayable decisions, checkpoints, trace validation |
| Native events first, OTel by conversion | One authoritative record; OTel is a view of it |
