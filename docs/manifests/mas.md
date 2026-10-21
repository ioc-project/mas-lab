<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# MAS manifest (`kind: MAS`)

**Package:** `mas-runtime` · **Schema:** `mas.schema.yaml` · **apiVersion:** `mas/v1`

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

At run time, the flavour's `agent_comm` block selects agent-to-agent transport ([flavour.md](flavour.md)). `spec.transport` does not.

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

Declares the entry point and the delegation graph. The embedded `MAS.spec.workflow` has `entry` and `nodes`. It has no `type` or `edges` field. The entry agent's `design_pattern` controls its reasoning and delegation.

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
| `delegates_to` | `string[]` | no | `[]` | IDs of agents this node may delegate to. Sets the `delegate_to_<id>` tools that the entry agent's LLM sees. |
| `role` | `string` | no | — | Role label, informational. |
| `agent` | `string` | no | — | Agent id override, for when the node id differs from the agent id. |
| `dispatch` | `string` | no | — | Fan-out mode. `parallel` (or `all`) runs this node's `delegates_to` peers concurrently. Any other value, or none, leaves delegation to the LLM. |
| `config` | `object` | no | `{}` | Plugin-specific parameters. Keys depend on the pattern. |
| `description` | `string` | no | `""` | Node description, informational. |

---

### Transport

_Used by:_ `spec.transport`

Declares how agents communicate within the MAS. The schema accepts this block. `mas-ctl run-mas` does not read it in this release. Delegation runs over the agent-to-agent plugin that the flavour's `agent_comm` selects (in-process by default).

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

`mas-ctl run-mas` does not read this block in this release. Set where events go with the agents' `spec.observability` (the `native` sink's `path` and `events_file`) or the flavour's `telemetry` block.

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
| **Workflow** | Entry agent, nodes, allowed delegation targets, and optional dispatch. | `spec.workflow` | Declares the delegation graph. It defines no `workflow.type` strategy. |
| **Routing logic** | Per-message decisions inside a turn: which tool or delegate comes next. | LLM, delegation tools, agent prompts | The moderator reads the user message and chooses `schedule_agent` or `itinerary_agent`. |

The workflow declares the entry agent and the delegation tools available. Three modes exist in one user turn:

- **LLM-directed:** the entry agent chooses among its declared `delegates_to` peers.
- **Deterministic linear:** the entry agent uses `design_pattern: deterministic_linear`. It calls its peers one after another with no LLM routing decision. Each peer receives the previous peer's output.
- **Parallel:** a node with `dispatch: parallel` fans out to its declared peers.

Routing logic is what happens inside the moderator's turn when it picks the next move, such as "a transport question, so delegate to `schedule_agent`". Routing is per message and per LLM step.
The embedded MAS workflow declares `entry`, `nodes`, `delegates_to`, and `dispatch`. Explicit sequential `edges` exist only in the standalone `Workflow` manifest below.

Overlays change the graph, the dispatch mode, or the entry agent's design pattern without editing the base agent manifests.

### Topology in paper labs

[`labs/design-space.lab/02-topologies/`](https://github.com/outshift-open/mas-lab/tree/main/labs/design-space.lab/02-topologies) varies topology only through scenario overlays. This is `topo-moderator-broker`:

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
| `dispatch: parallel` | A node with `dispatch: parallel` (or `all`) fans out to its `delegates_to` peers in one act. Parallel tool-call batches run concurrently on the async engine path (`ainvoke` / `afeed`). The sync path drains the same batch sequentially. |
| Fixed sequence | The entry agent uses `design_pattern: deterministic_linear` (or `deterministic_parallel` / `deterministic_single`). The embedded MAS workflow has no `edges`. |
| Single agent | The `topo-single-agent` overlay gives one generalist and no inter-agent workflow. |

### Concurrency model

The runtime is synchronous by default. Single-agent CLI chat, unit tests, and the benchmark harness call `invoke`, `feed`, and `run_turn`.
Concurrent fan-out uses async twins of those calls (`ainvoke`, `afeed`, `arun_turn`, `adelegate`, `aspawn`) on one cooperative event loop. Fan-out means parallel tool calls, `dispatch: parallel` peers, and concurrent `spawn_subagent`.

LLM providers must implement `achat_completion` as the async twin of `chat_completion`. A missing implementation fails at plugin load. The runtime does not run it on a worker thread, because OS-thread parallelism would break the lock-free run state (`SpawnLedger`, `InProcessCommBus`, `WorkingMemoryRegistry`).

OSS has no `WorkflowContract.register_impl()`. Topology and workflow are declarative YAML. ctl composes and runs them.

**Stateful governance** is separate from topology. Governance plugins track session state across turns. [`lifecycle-control.lab`](https://github.com/outshift-open/mas-lab/tree/main/labs/lifecycle-control.lab) stacks budget caps, guardrails, and HITL (human in the loop approval). See [governance.md](governance.md), the [contracts reference](../references/contracts.md#governance-oss), and [`budget.py`](https://github.com/outshift-open/mas-lab/blob/main/runtime/src/mas/runtime/boundary/gov/budget.py) (`spec.budget` call ceilings).

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
