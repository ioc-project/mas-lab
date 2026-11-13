<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin id cards (MAS Library Standard)

Each card below describes one plugin: its identity, what it implements and which manifest key selects it. Use it to find the id or alias to put in a manifest. Each plugin has a canonical `plugin_id@version`. Short names resolve via the runtime alias manifest in [`runtime/src/mas/runtime/aliases.yaml`](../../../../../../runtime/src/mas/runtime/aliases.yaml).

## Template

| Field | Example |
|-------|---------|
| **ID** | `react@v1` |
| **Alias** | `react` |
| **Kind** | design_pattern \| memory \| workflow \| tool \| governance \| observability |
| **Implementation** | Native Mealy plugin in `mas.runtime` |
| **TLA** | `ReactPattern.tla` |
| **Wraps** | None (not LangChain) |
| **Manifest keys** | `spec.design_pattern.type`, `--pattern` |
| **Used by** | Tutorial 01 default, design-space lab |

---

## Design patterns (`mas.runtime.machines.design_pattern`)

| ID | Alias | TLA | Implementation |
|----|-------|-----|----------------|
| `react@v1` | react | ReactPattern.tla | Native — reference Mealy δ |
| `cot@v1` | cot | CoTPattern.tla | Extends react, extra LLM pass |
| `introspection@v1` | reflection | CoTPattern.tla | min 2 passes (critique) |
| `plan_execute@v1` | plan_execute | DesignPatternScheduler.tla | JSON plan → kernel tool schedule |
| `tree_of_thoughts@v1` | tot | DesignPatternScheduler.tla | Multi-pass thought scoring |
| `single_pass@v1` | linear | — | One LLM call, no tool loop |

## Memory

| ID | Alias | TLA | Implementation |
|----|-------|-----|----------------|
| `memory-semantic@v1` | memory | MemoryMachine.tla | SQLite FTS5 (`boundary/memory/semantic.py`) |
| memory seeds | — | — | YAML overlay + `MemorySeedLoader` |

## Workflow (`mas.ctl.orchestration`)

| ID | Alias | Release | Notes |
|----|-------|---------|-------|
| `workflow-sequential@v1` | workflow-sequential | 2026.1 | Topological DAG |
| `workflow-graph@v1` | workflow-graph | 2026.1 | Graph topology |
| `workflow-supervised@v1` | workflow-supervised | 2026.1 | Operator approve between nodes |

## LLM providers (`mas.library.standard.plugins.llm`)

Same procedure as tools: a plugin speaks **one protocol**; the registry routes
`spec.models[].kind` (like `spec.providers[].kind`) to that plugin.

| ID | Role | Implementation |
|----|------|----------------|
| `openai` | Wire protocol | OpenAI-compatible `/chat/completions` HTTP |
| `cache` | Wrapper (not a protocol) | Disk cache around a routed protocol plugin |

Infra `spec.protocol` / agent `spec.models[].kind` select the wire protocol (default `openai`). Ollama and LiteLLM proxies use the OpenAI plugin. Offline CI replays a recorded live protocol through `llm_cache` (`raise_on_miss`).

## Agent communication (`mas.library.standard.plugins.agentcomm`)

| ID | Role | Implementation |
|----|------|----------------|
| `local` | In-process turn bus | `LocalAgentComm` |
| `local-group` | Unicast, anycast, rooms | `LocalGroupAgentComm` |
| `stack` | Bottom-up layer bind | `StackAgentComm` |
| `trace_context` | Child `traceparent` | `TraceContextLayer` |

- Card: [agentcomm/local-group.md](agentcomm/local-group.md)
- Card: [agentcomm/stack.md](agentcomm/stack.md)
- Card: [agentcomm/trace-context.md](agentcomm/trace-context.md)
- Docs: [comm-stack.md](../../../../../../docs/references/comm-stack.md)

## System plugins (`spec.system_plugins`)

| ID | Advertised tools | Implementation |
|----|------------------|----------------|
| `send` | `delegate_to_<id>` (not `send`) | `SendPlugin` |
| `inform_user` | `inform_user` | `InformUserPlugin` |
| `request_human_input` | `request_human_input` | `RequestHumanInputPlugin` |
| `pause_session` | `pause_session` and the control bundle | `PauseSessionPlugin` |
| `subagents` | `create_subagent` | `SubagentsPlugin` |

- Card: [system_tools/send.md](system_tools/send.md)
- Docs: [system-plugins.md](../../../../../../docs/manifests/system-plugins.md)

## Control protocol (`mas.library.standard.plugins.control`)

JSON-lines unix/TCP attach for `ControlContract`. This is **not** A2A.
Two people talk to an agent over A2A (`contextId` = session id). This
plugin is how an operator process attaches to the control plane.

| ID | Role | Implementation |
|----|------|----------------|
| `rpc` | Wire protocol | `ControlRpcProtocol` — advertise, heartbeat, unix/TCP, fail closed. Attach key is the session id (A2A `contextId`). |

Thinking depth, thinking-token budget, and hiding chain-of-thought live on
`spec.models[].reasoning` (`effort`, `budget_tokens`, `exclude`) — see
[`docs/manifests/llm-reasoning.md`](../../../../../../docs/manifests/llm-reasoning.md).

## Tools (library-samples)

Tutorial and benchmark tools live in **`library-samples`** as `kind: Tool` manifests
(e.g. `samples:tools/calc.tool.yaml`, `samples:tools/memory-search.tool.yaml`).
Agents reference them via ``spec.tools[].ref``; the runtime loads implementations
from the manifest, not from `mas.runtime`.

## Governance

| ID | Alias | Implementation |
|----|-------|----------------|
| `gov_no_undeclared_tool` | `gov_no_undeclared_tool` | `NoUndeclaredToolPlugin` — BLOCK names not in this LLM call's `tools` list; chain rule (pass or stop) |
| `gov_no_undeclared_peer` | `gov_no_undeclared_peer` | `NoUndeclaredPeerPlugin` — BLOCK leftover `delegate_to_*` dest not in this agent's `delegates_to` |
| `retry_on_error@v1` | `retry_on_error` | `RetryOnErrorPlugin` — ingress `error_policy` classifier; egress PASSes |

| `debug_script@v1` | `debug_script` | `DebugScriptPlugin` — gdb-like breakpoints on tool calls and results; observes, never blocks |

Card: [governance/debug-script.md](governance/debug-script.md).
Example: [examples/governance/debug-script/](../../../../../examples/governance/debug-script/).

- Card: [governance/no-undeclared-tool.md](governance/no-undeclared-tool.md)
- Card: [governance/no-undeclared-peer.md](governance/no-undeclared-peer.md)
- Card: [governance/retry-on-error.md](governance/retry-on-error.md)
- Example (not an app): [examples/governance/undeclared-tool/](../../../../../examples/governance/undeclared-tool/)

`spec.governance` is a chain (BLOCK exits, ALLOW continues). `spec.observability` is a sequence.

## Context (`summarizer` sub-plugin)

| ID | Alias | Implementation |
|----|-------|----------------|
| `llm` / `drop` | `summarizer` | `LlmSummarizer` / `DropSummarizer` — compress or drop older turns |

- Card: [context/summarizer.md](context/summarizer.md)
- Example (not an app): [examples/context/summarizer-override/](../../../../../examples/context/summarizer-override/)
- Overlay: `pkg://mas.library.standard/overlays/cheap-summarizer.yaml`
- Docs: [summarization.md](../../../../../../docs/manifests/summarization.md)
