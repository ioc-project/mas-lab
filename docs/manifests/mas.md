<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS manifest (`kind: MAS`)

**Package:** `runtime/` · **Schema:** `mas.schema.yaml` · **apiVersion:** `mas/v1`

A **MAS** (multi-agent system) manifest (`mas.yaml`) declares a team of **agents** and the **workflow** between them: who delegates to whom. Delegation means one agent hands a task to another through a tool call.
Write one when two or more agents cooperate, or when an **experiment** needs a named application to run.
An experiment points at a MAS. Its **scenarios** apply **overlays** (YAML patches) to change the topology (the team shape) or governance.

Minimal working MAS:

```yaml
apiVersion: mas/v1
kind: MAS
metadata:
  name: trip-planner
spec:
  agency:
    agents:
      - id: broker
        ref: ./agents/broker.yaml
      - id: flights
        ref: ./agents/flights.yaml
  workflow:
    entry: broker
    nodes:
      - id: broker
        delegates_to: [flights]
      - id: flights
```

Terms: [glossary.md](../glossary.md). Hub: [README.md](README.md).

## Contents

- [Top-level fields](#top-level-fields), [`metadata` fields](#metadata-fields), [`spec` fields](#spec-fields)
- [Sub-schemas](#sub-schemas): `intent`, ModelEntry, Agency, AgentEntry, Workflow, WorkflowNode, Transport, Framework, MemoryStores, Telemetry
- [Topology, workflow, and routing](#topology-workflow-and-routing), [Concurrency model](#concurrency-model)
- [Standalone workflow manifest](#standalone-workflow-manifest-kind-workflow)
- [Schema source](#schema-source)

---

## Top-level fields

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `apiVersion` | `string` | yes | Must be `mas/v1`. |
| `kind` | `string` | yes | Must be `MAS`. |
| `metadata` | object | yes | Identity and defaults. See [`metadata` fields](#metadata-fields). |
| `spec` | object | yes | Runtime behaviour. See [`spec` fields](#spec-fields). |
| `intent` | object | no | What this system knows and can do. See [`intent`](#intent). Use this top-level form in a base manifest. Overlays patch it through `spec.intent`. |

Extension properties (`x-*`) are allowed at the top level. The runtime ignores them.

---

## `metadata` fields

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `name` | `string` | **yes** | — | Unique MAS identifier. Used in run output paths and agent URNs. |
| `version` | `string` | no | `"0.1.0"` | Semver string (`major.minor.patch`). |
| `description` | `string` | no | `""` | Human-readable description. |
| `tags` | `string[]` | no | `[]` | Free-form tags for filtering and grouping. |
| `default_flavour` | `string` | no | `"local"` | Name of the flavour to use when none is specified on the CLI. |

Extension properties (`x-*`) are allowed and ignored by the runtime.

---

## `spec` fields

No fields are required; an empty `spec: {}` is valid.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `models` | `ModelEntry[]` | `[]` | Default model slots inherited by agents; `model: any` resolves through experiment and workspace defaults. See [`ModelEntry`](#modelentry). |
| `agency` | object | — | Participants and delegation targets. See [`Agency`](#agency). |
| `agents` | array | `[]` | Short-form agent references or inline Agent definitions. Same effect as `agency.agents` on a base manifest. In a MAS overlay, `spec.patch.agents` accepts per-agent patches or `$op` collection operations. |
| `workflow` | object | — | Entry point and delegation graph. See [`Workflow`](#workflow). |
| `transport` | object | — | Communication protocol config (not read at run time in this release). See [`Transport`](#transport). |
| `framework` | object | — | Framework surface adapter. See [`Framework`](#framework). |
| `tools_ref` | `string` \| null | `null` | MAS-level logical tool-set name (lowercase id, no paths or extensions) for an infra `ToolRegistry`. Validated only; this release does not load tools from it. Declare tools on each agent's `spec.tools`. |
| `memory_stores` | object | — | Named memory store artifact paths. The lab validator checks the files exist. Not read at run time. See [`MemoryStores`](#memorystores). |
| `telemetry` | object | — | Telemetry output config. `mas-ctl run-mas` does not read it. See [`Telemetry`](#telemetry). |
| `params` | `object` | `{}` | Free-form string key/value params for lab and benchmark tooling. The runtime kernel does not consume them. |
| `capabilities` | `object` | `{}` | Free-form capability declarations that manifest loading reads. The runtime kernel does not consume them. |
| `intent` | object | `{}` | Overlay-patchable intent block. In a base manifest, use the top-level `intent` field instead. |
| `middleware` | — | `null` | Reserved. |

#### Not used yet

- `transport`: `mas-ctl run-mas` does not read it.
- `tools_ref`: the schema validates it, and this release loads no tools from it.
- `memory_stores`: the lab validator checks the paths. The runtime does not load them.
- `telemetry`: `mas-ctl run-mas` does not read it.

At run time, the Flavour selects agent-to-agent transport: its `spec.bind.agent_comm` names the infra rows calls ride ([flavour.md](flavour.md#bind)). With no chain, calls use the library default `agent_comm` plugin (`local`, in-process). `spec.transport` does not select it, and the MAS has no `agent_comm` field.

---

## Sub-schemas

### `intent`

_Used by:_ top-level `intent` (and `spec.intent` for overlays)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `summary` | `string` | `""` | Short description of what this system knows and can do. Used in emulation and agent cards. |

**Example:**

```yaml
intent:
  summary: "Books travel: flights, hotels, and itineraries."
```

---

### ModelEntry

_Used by:_ `spec.models[]`

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `model` | `string` | **yes** | — | LiteLLM model id or `any` to inherit the resolved model slot. |
| `id` | `string` | no | `main` | Model slot identifier. |
| `temperature` | `number` | no | — | Sampling temperature, from `0` to `2`. |
| `max_tokens` | `integer` | no | — | Completion token limit; must be at least `1`. |
| `max_completion_tokens` | `integer` | no | — | Explicit answer + reasoning cap; wins over `max_tokens`. |
| `on_truncation` | string or object | no | — | Action on `finish_reason: length`; see [agent.md — Output-token limits](agent.md#output-token-limits). |
| `context_window` | `integer` | no | — | Model context-window size; must be at least `1`. |

The MAS row is a subset of the Agent [`ModelEntry`](agent.md#modelentry). Other sampling and reasoning fields belong on agent rows.

Model resolution runs in this order:

1. An agent that omits `spec.models`, or sets `model: any`, inherits the MAS primary entry (`id: main`).
2. A MAS `model: any` falls back to the experiment's `models.main` or `model`.
3. Then workspace `config.yaml` `defaults.model` applies.

---

### Agency

_Used by:_ `spec.agency`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `agents` | `AgentEntry[]` | `[]` | Ordered list of agent participants. See [`AgentEntry`](#agententry). |

---

### AgentEntry

`spec.agency.agents[]` and the short-form `spec.agents[]` have different reference requirements.

#### Agency entry (`spec.agency.agents[]`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `string` | **yes** | Logical agent id used by workflow nodes and delegation targets. |
| `ref` | `string` | no | Path to an Agent manifest relative to the MAS manifest. Omit for an external agent resolved through an infra Application endpoint. |

#### Short-form entry (`spec.agents[]`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ref` | `string` | **yes** | Path to an Agent manifest. |
| `id` | `string` | no | Logical agent id; derived from the referenced manifest when omitted. |

#### Form B — inline agent definition

Either list also accepts a full embedded `kind: Agent` manifest, with the same fields as a standalone `agent.yaml`. `kind`, `metadata`, and `spec` are required. `apiVersion` is optional.

**Example:**

```yaml
agency:
  agents:
    - id: broker
      ref: ./agents/broker.yaml       # Form A
    - kind: Agent                      # Form B — inline
      metadata:
        name: summariser
      spec:
        description: "Summarises findings."
```

---

### Adding or removing MAS agents with overlays

In an overlay that targets a MAS, `spec.patch.agents.$op` edits `spec.agency.agents`:

```yaml
target:
  kind: MAS
patch:
  agents:
    $op:
      remove: [schedule_agent]
      add:
        - id: generalist
          ref: ./agents/generalist.yaml
```

`add` and `replace` entries require both `id` and `ref`. `remove` takes agent ids. `clear: true` empties the participant list. These operations are valid only in an Overlay patch. `agents_add` and `agents_remove` are not schema fields.

---

### Workflow

_Used by:_ `spec.workflow`

Declares the entry point and the delegation graph. The embedded `MAS.spec.workflow` has `entry` and `nodes`. It has no `type` or `edges` field. `entry` is the agent that receives the user query. It does not change which leftover tools that agent offers. Each agent's leftover `delegate_to_<id>` tools come from its own `delegates_to` (deprecated, still implied). Set `behavior.delegation_style: none` on an agent to keep the topology and the send path without offering the model those tools. A linear, parallel, or single design pattern on an agent uses that agent's list as its next hop. An empty `delegates_to` means the model sees none of those tools. Hiding the tools is not a lock: a model call to a hidden `delegate_to_<id>` name for a declared peer still runs unless `gov_no_undeclared_tool` blocks it (it is in `with-hardened`).

| Field | Type | Description |
|-------|------|-------------|
| `entry` | `string` | ID of the entry agent, the first to receive the user request. |
| `nodes` | `WorkflowNode[]` | Agent nodes in the workflow graph. See [`WorkflowNode`](#workflownode). |

**Example:**

```yaml
workflow:
  entry: broker
  nodes:
    - id: broker
      delegates_to: [flights, hotels]
    - id: flights
    - id: hotels
```

---

### WorkflowNode

_Used by:_ `spec.workflow.nodes[]`

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `id` | `string` | **yes** | — | Node identifier. Must match an `agency.agents[].id`. |
| `delegates_to` | `string[]` | no | `[]` | IDs of leftover `delegate_to_<id>` tools offered to the model. A pattern that sends itself uses this list as the next hop. An empty list means none of those tools. Deprecated, still supported, no removal date. An empty list does not warn. |
| `role` | `string` | no | — | Role label, informational. |
| `agent` | `string` | no | — | Agent id override, for when the node id differs from the agent id. |
| `dispatch` | `string` | no | — | Deprecated, still accepted, ignored. Validation warns once and lists the nodes that set it. A node does not fan out to its peers; the model chooses parallel tool calls. |
| `config` | `object` | no | `{}` | Plugin-specific parameters. Keys depend on the pattern. |
| `description` | `string` | no | `""` | Node description, informational. |

`mas-ctl validate`, `compose`, and `run-mas` print one warning per compose when any node has a non-empty list. A second compose prints it again. `--no-validate` is silent. Overlay patches that set a non-empty list warn too.

---

### Transport

_Used by:_ `spec.transport`

Declares how agents communicate within the MAS. The schema accepts this block. `mas-ctl run-mas` does not read it in this release. Delegation runs in-process by default (the library default `agent_comm` plugin). A Flavour's `bind` links agents to infra rows. The Flavour's leftover `agent_comm` block is accepted and read by nothing.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `type` | `"local"` \| `"agent-remote"` \| `"agent-local"` | `"local"` | Communication type. |
| `mode` | `"local"` \| `"remote"` | `"local"` | High-level comm mode. |
| `emulation` | `boolean` | `true` | When `true`, delegation uses in-process function calls, with no HTTP. |

**Example:**

```yaml
transport:
  type: local
  emulation: true
```

---

### Framework

_Used by:_ `spec.framework`

Selects the framework adapter that wraps the native runtime. The lab infers the runner from `default_adapter` unless you override it at execution time.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_adapter` | `"native"` \| `"langgraph"` \| `"autogen"` \| `"crewai"` | `"native"` | Framework adapter id. `native` is the direct OSS kernel. `langgraph`, `autogen`, and `crewai` are reserved for wrappers that do not ship in this release. |

---

### MemoryStores

_Used by:_ `spec.memory_stores`

Named paths to shared memory store artifacts. All fields are optional strings. The lab validator checks that the files exist. The runtime does not load them in this release.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `episodic_ref` | `string` | `""` | Path to the episodic memory store artifact. |
| `semantic_ref` | `string` | `""` | Path to the semantic memory store artifact. |
| `procedural_ref` | `string` | `""` | Path to the procedural memory store artifact. |

---

### Telemetry

_Used by:_ `spec.telemetry`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `path` | `string` | `""` | Path to the event log file (JSONL), relative to `mas.yaml`. |

`mas-ctl run-mas` does not read this block in this release. Set where events go with a Control's `observability`, Flavour `bind.events`, or `--events*`. Leftover Flavour `observability` and `telemetry` are unread.

**Example:**

```yaml
telemetry:
  path: ./traces/events.jsonl
```

---

## Topology, workflow, and routing

Three related ideas are often confused:

| Term | What it is | Where it lives | Example |
|------|------------|----------------|---------|
| **Topology** | Which agents exist and how they relate (team shape). | `spec.agency.agents`, overlay patches | design-space Exp 1.2 sweeps five overlays (`topo-linear-pipeline`, `topo-moderator-broker`, `topo-parallel`, `topo-supervised`, `topo-verifier`) |
| **Workflow** | Entry agent, nodes, and allowed delegation targets. | `spec.workflow` | Declares the delegation graph. It defines no `workflow.type` strategy. |
| **Routing logic** | Per-message decisions inside a turn: which tool or delegate comes next. | LLM, delegation tools, agent prompts | The moderator reads the user message and chooses `schedule_agent` or `itinerary_agent`. |

The workflow declares the entry agent and the delegation tools available. Three modes exist in one user turn:

- **LLM-directed:** the entry agent chooses among its declared `delegates_to` peers.
- **Deterministic linear:** the entry agent uses `design_pattern: deterministic_linear`. It calls its peers one after another with no LLM routing decision. Each peer receives the previous peer's output.
- **Parallel:** the entry agent's model calls several `delegate_to_<id>` tools in one batch of parallel tool calls, or the entry agent uses `design_pattern: deterministic_parallel` to send every peer the same task in one batch.

Routing logic is what happens inside the moderator's turn when it picks the next move, such as "a transport question, so delegate to `schedule_agent`". Routing is per message and per LLM step.
The embedded MAS workflow declares `entry`, `nodes`, and `delegates_to`. A node's `dispatch` is deprecated and ignored. Explicit sequential `edges` exist only in the standalone `Workflow` manifest below.

Overlays change the graph or the entry agent's design pattern without editing the base agent manifests.

### Topology in paper labs

[`labs/design-space.lab/02-topologies/`](https://github.com/ioc-project/mas-lab/tree/main/labs/design-space.lab/02-topologies) varies topology only through scenario overlays. This is `topo-moderator-broker`:

```yaml
spec:
  target:
    kind: MAS
  patch:
    workflow:
      entry: moderator
      nodes:
        - id: moderator
          delegates_to: [schedule_agent, itinerary_agent, concierge_agent]
        - id: schedule_agent
        - id: itinerary_agent
        - id: concierge_agent
    agents:
      moderator:
        design_pattern:
          type: react
          config:
            max_steps: 10
```

### Workflow execution in OSS

| Pattern | ctl behaviour |
| --------- | --------------- |
| Dynamic delegation | Default multi-agent mode. The entry agent runs the session, and the LLM uses delegation tools. |
| Parallel fan-out | The entry agent's model chooses parallel tool calls, or the entry agent uses `design_pattern: deterministic_parallel`. Parallel tool-call batches run concurrently on the async engine path (`ainvoke` / `afeed`). The sync path drains the same batch sequentially. |
| Fixed sequence | The entry agent uses `design_pattern: deterministic_linear` (or `deterministic_parallel` / `deterministic_single`, `state_machine` for a table of states, or `moderated_rounds` for rounds a decider closes and a group confirms). The embedded MAS workflow has no `edges`. |
| Single agent | The `topo-single-agent` overlay gives one generalist and no inter-agent workflow. |

### Concurrency model

The runtime is synchronous by default. Single-agent CLI chat, unit tests, and the benchmark harness call `invoke`, `feed`, and `run_turn`.

**Many sessions in one process.** Each conversation is its own session object, with its own kernel and chat buffer. A service can run those sessions on separate threads, on one event loop (`arun_turn`), or on one loop per thread. A test runs eight sessions each way.

A2A `message/send` uses `contextId` as that session id. Pass it to continue a conversation. Omit it and a new conversation starts. A second message on the same `contextId` waits in a queue until the current turn finishes. Every `contextId` gets its own runtime, built again from the agent's manifest: its own kernel, context and engine. Conversations therefore run in parallel and share nothing, and each remembers its earlier messages. For a `run-mas` agent a conversation is a new copy of the whole MAS, wired like the run. A listener keeps at most 1024 conversations open and refuses a further new `contextId`. An agent started with an injected engine cannot be rebuilt, so it serves one conversation.

The LLM cache file, when you turn the cache on, is shared on purpose: the same prompt replays the same answer, so runs compare. Do not put the session id in the cache key. Pass your own `contextId` when you need a stable conversation id. The `llm_scheduler` limiter is also shared, so one process can keep a rate limit.

**Inside one session.** Concurrent fan-out uses the async twins (`ainvoke`, `afeed`, `arun_turn`, `adelegate`, `aspawn`) on one cooperative event loop. Fan-out means parallel tool calls and concurrent `spawn_subagent`. CLI chat waits for one turn to finish before the next line. A2A send queues. `run_turn` is the inner exclusive runner used by CLI and tests.

LLM providers must implement `achat_completion` as the async twin of `chat_completion`. A missing implementation fails at plugin load. The runtime does not run it on a worker thread: one session's fan-out stays on one event loop (`SpawnLedger`, `InProcessCommBus`).

A plugin tool sees the same thing wherever it runs: a frozen `ToolCallContext` (session, agent, user, turn, a copy of the spec). It cannot reach or change run state, so running it inline, in a thread or elsewhere gives the same result in the same order; only timing differs. Write a new tool as `async def on_execute_tool` (or `async def execute`). That one method is enough. A `def on_execute_tool` still works for existing plugins: the engine may run it in a worker thread by default. `on_aexecute_tool` still wins when present. That placement is set in the RuntimeEngine manifest, not on the tool: `engine.tool_worker` (`thread`, the default, or `inline`) and `engine.tool_concurrency` (default none: only the size of the batch limits it). A tool that must read or change session state (the skill tools) declares `kernel_state = True`; it gets the live ctx and always runs inline in the kernel.

OSS has no `WorkflowContract.register_impl()`. Topology and workflow are declarative YAML. ctl composes and runs them.

**Stateful governance** is separate from topology. Governance plugins track session state across turns. [`lifecycle-control.lab`](https://github.com/ioc-project/mas-lab/tree/main/labs/lifecycle-control.lab) stacks budget caps, guardrails, and HITL (human in the loop approval). See [governance.md](governance.md), the [contracts reference](../references/contracts.md#governance-oss), and [`budget.py`](https://github.com/ioc-project/mas-lab/blob/main/runtime/src/mas/runtime/boundary/gov/budget.py) (`spec.budget` call ceilings).

---

## Standalone workflow manifest (`kind: Workflow`)

Apps embed the workflow under `MAS.spec.workflow`. The standalone `kind: Workflow` manifest (`apiVersion: workflow/v1`) describes a workflow as a separate document with explicit routing edges.

In this release it is a schema only. `mas-ctl validate` and the controller check it. No MAS field references a Workflow document, and `mas-ctl run-mas` does not execute `edges` or `condition`. For a runnable fixed flow, use `MAS.spec.workflow` with a deterministic design pattern.

### Top-level fields

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `apiVersion` | `string` | yes | Must be `workflow/v1`. |
| `kind` | `string` | yes | Must be `Workflow`. |
| `metadata` | object | yes | May be empty; `name` and `description` are optional. |
| `metadata.name` | `string` | no | Workflow identifier. |
| `metadata.description` | `string` | no | Human-readable description. |
| `spec` | object | yes | Workflow body. |

### `spec` fields

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `entry` | `string` | `""` | ID of the entry agent. It receives the initial user prompt. |
| `nodes` | `WorkflowNode[]` | `[]` | Agent nodes. Same shape as [`WorkflowNode`](#workflownode), without `role`, `dispatch`, and `description`. |
| `edges` | `WorkflowEdge[]` | `[]` | Explicit routing edges. See [`WorkflowEdge`](#workflowedge). |
| `context_schema` | `object` | `{}` | Optional JSON Schema fragment describing shared context keys. |

### WorkflowEdge

_Used by:_ `spec.edges[]`

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `from` | `string` | **yes** | — | Source node ID. |
| `to` | `string` | **yes** | — | Target node ID. |
| `condition` | `string` \| null | no | `null` | Optional routing condition expression (not evaluated in this release). |
| `label` | `string` | no | `""` | Human-readable label describing when this edge fires. |

**Example:**

```yaml
apiVersion: workflow/v1
kind: Workflow
metadata:
  name: research-flow
spec:
  entry: moderator
  nodes:
    - id: moderator
      agent: moderator
      delegates_to: [researcher]
    - id: researcher
      agent: researcher
  edges:
    - from: moderator
      to: researcher
```

Schema source: `GET /api/schemas/workflow`

Topology-switching overlays replace `spec.patch.workflow` or `spec.patch.agents`. Experiments declare scenario overlays: [experiment.md](experiment.md). Patch files, target selectors, and grouped overrides: [overlay.md](overlay.md).

---

## Schema source

```bash
# From installed package
python -c "from mas.lab.schemas.paths import runtime_schema_dir; print(runtime_schema_dir() / 'mas.schema.yaml')"
python -c "from mas.lab.schemas.paths import runtime_schema_dir; print(runtime_schema_dir() / 'workflow.schema.yaml')"

# From Web UI / controller (default port 8090)
curl http://localhost:8090/api/schemas/mas
curl http://localhost:8090/api/schemas/workflow
```

---

## See also

- [Agent manifest](agent.md)
- [Tutorial: creating a MAS](../tutorials/02-creating-a-mas/README.md)
- [Standalone workflow manifest](#standalone-workflow-manifest-kind-workflow)
