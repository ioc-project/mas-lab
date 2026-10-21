<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Agent manifest (`kind: Agent`)

**Package:** `mas-runtime` · **Schema:** `agent.schema.yaml` · **apiVersion:** `mas/v1`

An **agent** manifest (`agent.yaml`) declares one LLM actor. It sets the agent's models, context, tools, skills, design pattern, governance, and **observability** (trace output) settings.
Write one for every agent you run alone or place in a **MAS** (multi-agent system).
A MAS manifest references agents by name. **Overlays** (YAML patches) change an agent without copying its file.

Minimal working agent:

```yaml
apiVersion: mas/v1
kind: Agent
metadata:
  name: broker
spec:
  description: "Broker agent. Routes requests to specialists."
```

Terms: [glossary.md](../glossary.md). Hub: [README.md](README.md).

## Contents

- [Top-level fields](#top-level-fields), [`metadata` fields](#metadata-fields), [`spec` fields](#spec-fields)
- Context: [Assembler](#assembler), [ContextSourcesBinding](#contextsourcesbinding), [ContextChunk](#contextchunk), [ContextManager](#contextmanager), [ContextManagerParams](#contextmanagerparams)
- Models: [ModelEntry](#modelentry), [Output-token limits](#output-token-limits), [LlmBinding](#llmbinding)
- Behaviour: [DesignPattern](#designpattern), [Behavior](#behavior), [Delegation](#delegation), [Tool](#tool)
- Memory: [MemorySeedEntry](#memoryseedentry), [MemoryConfig](#memoryconfig), [WorkingMemory](#workingmemory)
- Control: [GovernanceBinding](#governancebinding), [Budget](#budget), [ControlBinding](#controlbinding), [ObservabilityBinding](#observabilitybinding), [Debug](#debug)
- [Schema source](#schema-source)

---

## Top-level fields

Every manifest starts with four required top-level fields.

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `apiVersion` | `string` | yes | Must be `mas/v1`. |
| `kind` | `string` | yes | Must be `Agent`. |
| `metadata` | object | yes | Identity and tagging. See [`metadata` fields](#metadata-fields). |
| `spec` | object | yes | Runtime behaviour. See [`spec` fields](#spec-fields). |

Extension properties (`x-*`) are allowed at the top level. The runtime ignores them. They carry UI metadata and canvas state.

---

## `metadata` fields

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `name` | `string` | yes | — | Agent ID. Must match the workflow node id that references this agent. Must not contain `/` or `\`, because a file path is not a name. |
| `description` | `string` | no | `""` | Human-readable description of this agent. |
| `version` | `string` | no | `"0.1.0"` | Semver string (`major.minor.patch`). |
| `tags` | `string[]` | no | `[]` | Free-form tags for filtering and grouping. |

Extension properties (`x-*`) are allowed and ignored by the runtime.

---

## `spec` fields

`spec.description` is the only required field. A plugin is a class that a library registers and a manifest names ([plugin-bindings.md](plugin-bindings.md)).

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `description` | `string` | **yes** | — | One-line summary for delegation tools, the AgentCard, and registry discovery. Write it from the delegator's perspective. The runtime does **not** inject it into the LLM system prompt. Use `context.*` for prompt text. |
| `context` | `object` | no | — | Named chunks injected into the system prompt. You choose the keys (`role`, `intent`, ...). See [`ContextChunk`](#contextchunk). |
| `params` | `object` | no | `{}` | Free-form string key/value params forwarded to infra middleware. The kernel does not consume them. |
| `models` | `ModelEntry[]` | no | `[]` | Preferred models for this agent. See [`ModelEntry`](#modelentry). |
| `design_pattern` | object \| null | no | `react` | Reasoning strategy. See [`DesignPattern`](#designpattern). |
| `context_manager` | object | no | `summarising` | Keeps history inside the context window (the model's maximum input size). See [`ContextManager`](#contextmanager). |
| `assembler` | string \| object \| null | no | `assembler` | Builds the LLM `messages[]` from context. |
| `memory_seed` | `MemorySeedEntry[]` | no | — | Documents to index into memory at startup. See [`MemorySeedEntry`](#memoryseedentry). |
| `memory` | string \| string[] \| object | no | — | Memory backend configuration. Not used yet: the schema accepts it, the runtime ignores it. See [`MemoryConfig`](#memoryconfig). |
| `working_memory` | object | no | — | Cross-turn history persistence for delegated agents. See [`WorkingMemory`](#workingmemory). |
| `checkpoint` | object | no | `{mode: none}` | Session checkpoint (saved session state) cadence, retention, portability, and automatic resume. See [checkpoint.md](checkpoint.md). |
| `debug` | object | no | — | Script for the `debug_script` runtime plugin (gdb-like). See [`Debug`](#debug). |
| `skills` | `string[]` | no | `[]` | Skills to activate. A skill is a packaged instruction bundle the agent loads on demand. Use a bare name or `@library/name`. Resolution order: app-local, libraries, packages. See [system-tools.md](system-tools.md#implicit-enablement). |
| `tools_ref` | `string` \| null | no | `null` | Logical tool-set name for an infra `ToolRegistry`: a lowercase id such as `web-tools`, with no paths or extensions. Not used yet: the schema validates it, and this release loads no tools from it. |
| `tools` | `Tool[]` | no | `[]` | Per-agent tool declarations (a tool is a function the LLM can call), in four forms. See [`Tool`](#tool). |
| `behavior` | object | no | — | Runtime capability flags. See [`Behavior`](#behavior). |
| `governance` | `GovernanceBinding` | no | `[]` | Chain of plugins that allow, block, or modify calls. See [`GovernanceBinding`](#governancebinding). |
| `hitl_contract` | string \| object | no | `registry` | Human-in-the-loop (HITL) approval plugin. A plugin binding: a name or `{type, ref, params}`. An instance from the embedding host overrides it, which keeps interactive CLI adapters working. Shipped: `registry`, `auto_resolve`. |
| `user_io_contract` | string \| object | no | `registry` | Plugin that sends progress updates to the user. The host overrides it the same way as `hitl_contract`. |
| `llm` | `LlmBinding` | no | `{}` | **Deprecated** engine overrides (model, temperature, ...). Prefer `spec.models[]`. See [`LlmBinding`](#llmbinding). |
| `budget` | object | no | `{}` | Call-count ceilings (`max_llm_calls`, `max_tool_calls`) that the engine enforces. Omitted means no limit. See [`Budget`](#budget). |
| `control` | `ControlBinding` | no | `{}` | Retry, circuit-breaker, and rate-limit config. See [`ControlBinding`](#controlbinding). |
| `observability` | `ObservabilityBinding` | no | `null` | List of trace sink plugins. See [`ObservabilityBinding`](#observabilitybinding). |
| `context_sources` | `ContextSourcesBinding` | no | `null` | Context source plugins, including the skill engine. See [`ContextSourcesBinding`](#contextsourcesbinding). |

#### Not used yet

- `memory`: the schema accepts it, the runtime ignores it.
- `tools_ref`: the schema validates it, and this release loads no tools from it.

---

## Sub-schemas

### Assembler

_Used by:_ `spec.assembler`

The assembler builds the LLM `messages[]` from context. The schema accepts a plugin-name string, `null`, or an object with `type` or `ref`. The only shipped assembler is `assembler` (aliases `context-assembler`, `ctx-assembler`). Its `params`:

| Param | Default | Description |
|-------|---------|-------------|
| `token_budget` | unset | Maximum estimated tokens for injected context parts; unset means no part eviction. |
| `always_reassemble` | `false` | Re-inject context parts on every LLM call. By default they are injected once per turn. History management runs on every call either way. |
| `emit_segments` | `true` | Emit `context_assembled` and per-part provenance events, so traces show which context parts each call received. |

History policy is not an assembler param. It lives on `spec.context_manager`. See [plugin-bindings.md](plugin-bindings.md#assembler-default-assembler).

```yaml
assembler:
  type: assembler
  params: {token_budget: 6000}
```

### ContextSourcesBinding

_Used by:_ `spec.context_sources`

An ordered list of context-source plugin ids. Today they select the skill engine (`native`, `adk`, or `langchain`, from `mas-library-skills`) that loads the skills listed in `spec.skills`. An entry is a bare id or a single-key object with these options:

| Option | Default | Description |
|--------|---------|-------------|
| `base_dir` | — | Directory to load skills from. |
| `auto_inject` | `false` | Grant `run_skill_script` even if no listed skill ships `scripts/`. |
| `pin_activated` | `true` | Keep activated skill bodies pinned in the system prompt. |
| `allow_unload` | `false` | Advertise `activate_skill(name, unload=true)`. Off by default because the Agent Skills spec has no unload. |
| `auto_load` | `false` | Activate every listed skill at session start instead of waiting for `activate_skill`. |

```yaml
context_sources:
  - native:
      auto_load: true
```

### ContextChunk

_Used by:_ `spec.context.<key>`

Each value under `spec.context` is a named chunk. The runtime injects it into the system prompt under `[key]`. Three forms exist:

| Form | Type | Description |
|------|------|-------------|
| Inline string | `string` | Injected as literal text, unless the value resolves to an existing file under the agent manifest directory (such as `prompts/role.md` or `./prompts/role.md`). |
| File reference | `{ ref: string }` | Always loads the file at `ref`, relative to the agent manifest directory. A missing path raises `ContextRefNotFoundError` at bootstrap. |
| Fragment list | list of strings and/or `{ ref }` | Fragments are resolved and joined with newlines. An overlay can add or remove one fragment with `$op` ([overlay.md](overlay.md#merge-semantics)). |

**Example:**

```yaml
context:
  role: |
    You are a telemetry analyst…
  intent:
    ref: "./prompts/intent.md"
```

---

### ModelEntry

_Used by:_ `spec.models[]`

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `model` | `string` | **yes** | — | LiteLLM-style model string, such as `vertex_ai/gemini-3-pro-preview`. Use `any` to inherit the MAS, experiment, or workspace default model. |
| `id` | `string` | no | `"main"` | Logical model ID within this agent. Use `main` for the primary model. |
| `kind` | `string` | no | — | Wire-protocol plugin for model calls (`openai` is shipped). |
| `temperature` | `number` [0.0–2.0] | no | `0.7` | Sampling temperature. |
| `max_tokens` | `integer` ≥ 1 | no | unset | Output-token budget; omitted means no limit is sent. |
| `max_completion_tokens` | `integer` ≥ 1 | no | unset | Explicit cap on answer plus reasoning. The request never carries both token-limit fields. |
| `on_truncation` | string or object | no | `warn` | Action when the provider returns `finish_reason: length`. See [Output-token limits](#output-token-limits). |
| `context_window` | `integer` ≥ 1 | no | `128000` | Input context-window size for model-aware assembly. Compile fills it from the model catalog. An unknown model uses `128000`. |
| `reasoning` | object | no | — | Thinking controls: `effort`, `budget_tokens`, `exclude`, `mode`, `think`, `include`. See [llm-reasoning.md](llm-reasoning.md). `reasoning_effort` is a deprecated alias for `reasoning.effort`. |
| `extra` | object | no | — | Protocol-specific fields forwarded on the request body. |

A row also accepts these static Chat Completions sampling fields: `top_p`, `top_k`, `min_p`, `min_tokens`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `seed`, `n`, `stop`, `logit_bias`, `logprobs`, `top_logprobs`, `parallel_tool_calls`, `response_format`, `verbosity`, `service_tier`, `store`, `metadata`, `modalities`, `prediction`, `prompt_cache_key`, and `user`. The schema is [`llm-sampling.schema.yaml`](../schemas/runtime/fragments/llm-sampling.schema.yaml).
The [model catalog](llm-model-catalog.md) records which fields each model honours.

Do **not** put `api_base` or `api_key_env` here. They belong in the flavour or infra manifest.

**Example:**

```yaml
models:
  - model: vertex_ai/gemini-3-pro-preview
    temperature: 0.3
    max_tokens: 4096
```

#### Output-token limits

By default, requests send neither `max_tokens` nor `max_completion_tokens`. The provider's server default applies.
The runtime lowers a configured budget per call so that the estimated prompt plus completion fits `context_window`. The effective cap is also bounded by the lower of infra `spec.models.generation.max_output_tokens` and the model catalog's `max_output_tokens`.

| Setting | Meaning |
|---------|---------|
| `max_tokens` | Output budget; reasoning models may map it to `max_completion_tokens`. |
| `max_completion_tokens` | Explicit answer plus reasoning budget. Takes precedence over `max_tokens`. |
| `on_truncation` | `ignore`, `warn` (default), `error`, or `escalate`. Object form accepts `factor` (default 2), `max_tokens`, and `retries` (default 1). Retries count against `spec.budget.max_llm_calls`. |

The CLI alias `--max-tokens N` expands to `agent:spec.models[*].max_tokens=N`. For example, `mas-ctl chat agent.yaml --max-tokens 4096` equals `--override 'agent:spec.models[*].max_tokens=4096'`. Explicit `--override` arguments apply afterward.
The environment variables `MAS_LLM_MAX_TOKENS`, `MAS_LLM_MAX_COMPLETION_TOKENS`, and `MAS_LLM_ON_TRUNCATION` fill in settings that the model row does not declare.

LLM traces include `finish_reason`, the final `max_tokens` budget, completion token usage, and `truncation_retries`. See also [infra.md, LLM output-token policy](infra.md#llm-output-token-policy) and [CLI overrides](../cli/overrides.md).

---

### DesignPattern

_Used by:_ `spec.design_pattern`

Selects the agent's reasoning strategy. It defaults to `react` when absent. The value is a plugin-name string (`design_pattern: cot`), `null` (the default), or an object:

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `type` | `string` | mutually exclusive with `ref` | Registered plugin name. Shipped: `react`, `cot`, `single_pass`, `introspection`, `plan_execute`, `tree_of_thoughts`, `deterministic_single`, `deterministic_linear`, `deterministic_parallel`, plus aliases (`analyst`, `planner`, `linear`, `reflection`, `tot`, `plan-execute`). |
| `ref` | `string` | mutually exclusive with `type` | Same lookup as `type` (name, alias, or plugin URN such as `mas.dp.react`). File paths and `module://` / `oci://` locators are not resolved in this release. |
| `params` | `object` | no | Plugin parameters. Common keys: `max_steps` (dispatch-loop cap, default none), `max_cot_pass` (default `1`), `parallel` (parallel tool calls, default `true`). See [plugin-bindings.md](plugin-bindings.md#design_pattern-default-react). |
| `config` | `object` | no | Alias for `params` (overlay compatibility); `params` wins when both are set. |

To use your own pattern, register the class as `type: design_pattern` in a library manifest ([Libraries](../libraries.md)) and name it here.

**Example:**

```yaml
design_pattern:
  type: react

# or a pattern registered by your own library:
design_pattern:
  type: my_cot
  params:
    max_steps: 10
```

---

### ContextManager

_Used by:_ `spec.context_manager`

Controls how conversation history stays within the model's context window. It defaults to `summarising`. The value is a plugin-name string or an object:

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `type` | `string` | mutually exclusive with `ref` | Shipped: `summarising` (alias `summarizing`), `sliding-window` (alias `sliding_window`), `stack` (alias `full-history`). |
| `ref` | `string` | mutually exclusive with `type` | Same lookup as `type` (name, alias, or plugin URN such as `mas.cm.stack`). |
| `params` | `ContextManagerParams` | no | Strategy-specific parameters. See [`ContextManagerParams`](#contextmanagerparams). |
| `memory` | `string[]` | no | Memory types to inject as context facets, such as `[semantic]`. Not used yet: the shipped context managers do not read it. |

Strategies:

- `summarising` keeps the last `keep_turns` turns verbatim. It replaces older ones with an LLM summary. This is compaction.
- `sliding-window` drops turns older than the window.
- `stack` keeps everything, or the last `max_messages`.

Full reference: [context-assembly.md](context-assembly.md) and [summarization.md](summarization.md).

**Example:**

```yaml
context_manager:
  type: sliding-window
  params:
    window_size: 20
```

---

### ContextManagerParams

_Used by:_ `spec.context_manager.params`

The `ContextManagerPlugin` receives these as constructor kwargs. All fields are optional.

| Field | Type | Description |
| ------- | ------ | ------------- |
| `max_turns` | `integer` ≥ 1 | Sliding-window: maximum prior user turns. Alias for `keep_turns`. |
| `window_size` | `integer` ≥ 1 | Sliding-window: alias for `keep_turns`. |
| `max_messages` | `integer` ≥ 1 | Stack: cap on total past messages. |
| `summary_threshold` | `integer` ≥ 0 | Summarising: token estimate that triggers compaction. `0` uses the derived model context window. |
| `keep_turns` | `integer` ≥ 1, default `10` | Summarising: recent user turns kept verbatim. |
| `hysteresis_ratio` | number [0–1], default `0.1` | Summarising: reuse the cached summary until the history nears the safety-bounded hard budget. |
| `summarizer` | string \| object | Sub-plugin: `llm` or `drop`. The object form can set the model and instructions. |
| `working_memory_messages` | `integer` ≥ 1, default `20` | Assembly cap on in-turn working-memory messages. |
| `max_in_turn_messages` | `integer` ≥ 1 | Alias for `working_memory_messages`. |
| `trimmer` | object | Assembly-time token trimming: `max_tokens` (alias `token_budget`; default the model's `context_window`), `reserve_tokens` (tokens kept for the completion; default the model's `max_tokens`, else `2000` in compiled output), and `safety_margin_ratio` (0–0.5, default `0.1`). |
| `working_memory_ref` | `string` | Registry ref for the working-memory context source plugin. |

The [context-manager parameter schemas](../schemas/runtime/fragments/context-manager-params.schema.yaml) define the nested `trimmer` and `summarizer` keys.

---

### MemorySeedEntry

_Used by:_ `spec.memory_seed[]`

Indexes a document into memory at startup. Use it for demos and tests that need pre-populated memory without a persistent store.
The runtime writes seeds into the agent's semantic memory store, which the `memory-search` sample tool queries. Context assembly also receives them. Overlay `memory_seed` patches append to the agent's existing seeds.

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `content` | `string` | **yes** | Document text to index. |
| `key` | `string` | no | Lookup key. |
| `source` | `string` | no | Source label / filename. |
| `id` | `string` | no | Explicit document ID. |
| `text` | `string` | no | Alias for `content`. |

**Example:**

```yaml
memory_seed:
  - source: "runbook.md"
    content: |
      ## Restart procedure
      1. Drain traffic…
```

---

### MemoryConfig

_Used by:_ `spec.memory`

Memory is stored knowledge an agent can retrieve across turns.

For working semantic memory today, use `spec.memory_seed` plus a memory tool (`memory-search` in `library-samples`).

That tool keeps its store in `$XDG_DATA_HOME/mas/memory/semantic/<agent_id>.sqlite` (default `~/.local/share/mas/memory/semantic/`), so facts persist across runs. `db_path` is a `SemanticMemoryPlugin` constructor argument that no manifest sets today.

Three forms are accepted:

| Form | Example | Description |
| ------ | --------- | ------------- |
| String shorthand | `memory: semantic` | Resolves to the corresponding plugin bundle. |
| Array shorthand | `memory: [semantic, episodic]` | Activates multiple named bundles. |
| Object form | `memory: {enabled: true, types: [...]}` | Full configuration — described below. |

**Object form fields:**

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `enabled` | `boolean` | `true` | Master switch. `false` loads no memory plugins. |
| `types` | `MemoryType[]` | `[]` | Ordered list of memory layers. See [`MemoryType`](#memorytype). |
| `persistence` | object | — | Session transcript persistence. See [`MemoryPersistence`](#memorypersistence). |
| `search` | object | — | Semantic retrieval configuration. See [`MemorySearch`](#memorysearch). |
| `citations` | object | — | Citation formatting for search results. See [`MemoryCitations`](#memorycitations). |
| `sync` | object | — | File-watching and re-indexing policy. See [`MemorySync`](#memorysync). |
| `overflow_retry` | object | — | Compaction retry on context overflow. See [`MemoryOverflowRetry`](#memoryoverflowretry). |
| `qmd` | object | — | QMD (Qualitative Memory Database) backend. See [`MemoryQMD`](#memoryqmd). |

#### Not used yet

The schema accepts `spec.memory`, and `mas-ctl chat --memory` writes it. The runtime does not read it when it builds an agent, and it loads no memory plugin from this block. The defaults in the tables below are the schema's, not observed behaviour. This applies to every `Memory*` table on this page.

#### MemoryType

_Used by:_ `spec.memory.types[]`

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `name` | `string` | **yes** | — | Layer name: `session` \| `episodic` \| `semantic` \| `working` \| `procedural`. |
| `backend` | `string` | no | `"in-memory"` | Backend: `in-memory` \| `file` \| `sqlite-vec` \| `redis` \| `remote_tool`. |
| `params` | `MemoryBackendParams` | no | `{}` | Backend constructor kwargs. See [`MemoryBackendParams`](#memorybackendparams). |

#### MemoryBackendParams

_Used by:_ `spec.memory.types[].params`

| Field | Type | Description |
| ------- | ------ | ------------- |
| `path` | `string` | Filesystem path (file backend). |
| `url` | `string` | Connection URL (redis, remote_tool). |
| `collection` | `string` | Collection / table name. |
| `host` | `string` | Hostname. |
| `port` | `integer` | Port number. |
| `database` | `string` | Database name. |

#### MemoryPersistence

_Used by:_ `spec.memory.persistence`

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `backend` | `string` | `"none"` | `none` \| `file` \| `sqlite` \| `redis`. |
| `path` | `string` | `""` | Base directory. Supports `{agent_id}` and `{session_id}` placeholders. Default: `$XDG_DATA_HOME/mas/agents/{agent_id}/sessions/`. |
| `auto_save` | `boolean` | `true` | Persist after each turn. |
| `auto_load` | `boolean` | `true` | Load session on bootstrap. |

#### MemorySearch

_Used by:_ `spec.memory.search`

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `enabled` | `boolean` | `false` | Enable semantic search injection into context. |
| `max_results` | `integer` | `6` | Maximum retrieved chunks per query. |
| `min_score` | `number` | `0.35` | Minimum similarity score threshold. |
| `chunking.tokens` | `integer` | `400` | Chunk size in tokens for indexing. |
| `chunking.overlap` | `integer` | `80` | Overlap tokens between chunks. |
| `hybrid.enabled` | `boolean` | `true` | Enable hybrid search (vector + full-text). |
| `hybrid.vector_weight` | `number` | `0.7` | Vector score weight in hybrid ranking. |
| `hybrid.text_weight` | `number` | `0.3` | Full-text score weight in hybrid ranking. |
| `hybrid.mmr_enabled` | `boolean` | `false` | Max Marginal Relevance re-ranking. |
| `hybrid.mmr_lambda` | `number` | `0.7` | MMR lambda (diversity vs relevance). |
| `hybrid.temporal_decay_enabled` | `boolean` | `false` | Apply time-decay to scores. |
| `hybrid.temporal_decay_half_life_days` | `integer` | `30` | Half-life for temporal decay. |
| `cache.enabled` | `boolean` | `true` | Cache search results. |
| `cache.max_entries` | `integer` | `128` | Maximum cached search results. |

#### MemoryCitations

_Used by:_ `spec.memory.citations`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `"auto"` \| `"on"` \| `"off"` | `"auto"` | Citation mode: `auto` (include when useful), `on` (always), `off` (strip source info). |
| `max_snippet_chars` | `integer` | `700` | Maximum characters per search result snippet. |

#### MemorySync

_Used by:_ `spec.memory.sync`

Controls when workspace memory files (`MEMORY.md`, `memory/*.md`) are re-indexed.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `on_session_start` | `boolean` | `true` | Re-index when a session begins. |
| `on_search` | `boolean` | `false` | Re-index before each search. |
| `interval_seconds` | `integer` | `300` | Periodic re-index interval in seconds (0 = disabled). |
| `delta_messages` | `integer` | `50` | Re-index after this many new messages. |
| `delta_bytes` | `integer` | `100000` | Re-index after this many bytes of new content. |
| `post_compaction_force` | `boolean` | `true` | Force re-index after compaction. |
| `file_watch.enabled` | `boolean` | `true` | Enable file change detection. |
| `file_watch.patterns` | `string[]` | `["MEMORY.md","memory/*.md"]` | Glob patterns to watch. |
| `file_watch.debounce_ms` | `integer` | `1500` | Debounce interval for file change events. |

#### MemoryOverflowRetry

_Used by:_ `spec.memory.overflow_retry`

When an LLM call fails because the context exceeds the window, compact and retry.

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `enabled` | `boolean` | `false` | Enable overflow detection and retry. |
| `max_retries` | `integer` | `2` | Maximum compaction + retry attempts. |
| `aggregate_timeout_seconds` | `number` | `60.0` | Maximum total time across all retries. |
| `budget_reduction_factor` | `number` | `0.7` | Multiply token budget by this factor on each retry. |

#### MemoryQMD

_Used by:_ `spec.memory.qmd`

Alternative search via external `qmd` binary (Qualitative Memory Database).

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `enabled` | `boolean` | `false` | Enable QMD as a retriever backend. |
| `binary_path` | `string` | `""` | Path to `qmd` binary (auto-detected if empty). |
| `index_path` | `string` | `""` | Path to QMD index directory. |
| `search_mode` | `string` | `"search"` | QMD search mode. |
| `max_snippet_chars` | `integer` | `700` | Maximum characters per snippet. |
| `timeout_seconds` | `number` | `4.0` | Search timeout in seconds. |

---

### WorkingMemory

_Used by:_ `spec.working_memory`

Controls whether a delegated agent keeps its committed conversation history (user and assistant turns) across separate delegation calls in one `mas-ctl` session. The key is `(session_id, agent_id)`.
The buffer lives in process only. It does not survive a restart. Only the delegation path reads it. An entry agent that the user drives directly keeps its own history across turns, whatever this field says. The runtime always rebuilds the system prompt from the manifest.

This differs from `spec.memory`, the retrieval-store subsystem (semantic, episodic, procedural search). Working memory is the raw turn buffer a delegated agent falls back on.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `persistent` | `boolean` | `true` | `true` reuses committed turn history across delegate calls in the same session. `false` clears working memory before every delegate call, for agents that must be stateless per call (a formatter or translator). |
| `compaction` | `WorkingMemoryCompaction` | — | How much history to keep as it grows. See [`WorkingMemoryCompaction`](#workingmemorycompaction). It is shorthand for `spec.context_manager`. Set `context_manager` directly for lower-level control. It wins if both are set. |

**Context ID:** the delegating agent can pass `context_id` with `task` on a `delegate_to_<agent_id>` call. It selects an independent working-memory bucket for that peer, such as `"trip-paris"` or `"trip-tokyo"` in one session. Omit it to use the session's default bucket.

#### WorkingMemoryCompaction

_Used by:_ `spec.working_memory.compaction`

| Field | Type | Default | Description |
| ------- | ------ | --------- | ------------- |
| `strategy` | `"keep_recent"` \| `"sliding_window"` \| `"summarize"` | `"keep_recent"` | Compaction strategy (see below). |
| `max_messages` | `integer` ≥ 1 | — | `keep_recent`: cap on total committed messages. |
| `window_size` | `integer` ≥ 1 | — | `sliding_window`: number of recent exchange pairs to keep. |
| `summary_threshold` | `integer` ≥ 0 | `0` | `summarize`: estimated-token threshold that triggers compaction. `0` or omitted uses the model `context_window` minus the completion reserve. |
| `keep_turns` | `integer` ≥ 1 | `10` | `summarize`: recent exchange pairs kept verbatim next to the summary. |
| `model` | `string` | — | `summarize`: model for the summary call, a `spec.models[].id` or a LiteLLM model string. |

Each strategy maps onto a context manager: `keep_recent` to `stack`, `sliding_window` to `sliding-window`, `summarize` to `summarising` ([plugin-bindings.md § Compaction](plugin-bindings.md#compaction)).

**Strategies:**

- `keep_recent` caps total messages at `max_messages`. It makes no LLM call.
- `sliding_window` keeps the last `window_size` exchange pairs. It makes no LLM call.
- `summarize` compresses older turns into one summary block and keeps `keep_turns` recent pairs verbatim. The summary uses `model` if set, else the summarizer model slot, else this agent's turn model. Without a live model, it drops older turns instead.

**Example:**

```yaml
working_memory:
  persistent: true
  compaction:
    strategy: summarize
    summary_threshold: 4000
    keep_turns: 10
```

---

### Tool

_Used by:_ `spec.tools[]`

A tool is a function the LLM can call. Four forms are accepted. You can mix them in one list.

#### Form A: manifest reference (recommended)

References a `kind: Tool` manifest file ([tool.md](tool.md)).

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `ref` | `string` | **yes** | — | Path to a `kind: Tool` manifest relative to this manifest (`./tools/calc.tool.yaml`), a `pkg://` path, or a library ref (`samples:tools/calc.tool.yaml`). `bundle://<bundle>/<tool>` passes validation but this release does not resolve it. |
| `priority` | `integer` | no | `100` | Registration priority. Higher loads first. |
| `params` | `object` | no | `{}` | Init kwargs merged into the referenced tool's `spec.impl.params`. |

#### Form B: inline anonymous

Declares a Python class, remote tool, or OpenAPI endpoint inline.

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `module_path` | `string` | **yes** | — | Dotted Python module path or relative file path (`./tools/my_tool.py`). |
| `kind` | `"python"` \| `"remote_tool"` \| `"openapi"` | no | `"python"` | Implementation kind. |
| `class_name` | `string` | no | — | Class name in the module. The runtime discovers it when omitted. |
| `priority` | `integer` | no | `100` | Registration priority. |
| `params` | `object` | no | `{}` | Tool-specific init params. |

#### Form C: semantic name

A bare string that the active infra or local tool catalog resolves (`web-search`, `calculator`, `memory-search`).

#### Form D: system tool

A tool the runtime owns, such as `request_human_input`, `inform_user`, `run_skill_script`, or `create_subagent`.

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `kind` | `"system"` | **yes** | — | Marks the entry as a system tool. |
| `name` | `string` | **yes** | — | System tool name. An unknown name fails at load time. |
| `enabled` | `boolean` | no | `true` | `false` turns the tool off, even when the host requests it. |
| `params` | `object` | no | `{}` | Tool-specific configuration. |

[system-tools.md](system-tools.md) lists which system tools other fields imply and what each does.

**Example:**

```yaml
tools:
  - ref: ./tools/calculator.tool.yaml          # Form A
  - module_path: library-samples/tools/calc.py  # Form B
    class_name: CalcTool
  - web-search                                  # Form C
  - kind: system                                # Form D
    name: request_human_input
    params: {timeout: 300}
```

---

### Behavior

_Used by:_ `spec.behavior`

Flags that set which system tools and capabilities the agent's LLM sees at run time.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `share_reasoning` | `boolean` | `false` | Planned: a sub-agent returns a short summary of how it reached its answer, next to the result. Not used yet: the schema accepts it and no shipped tool reads it. |
| `delegation_style` | `"typed"` | `"typed"` | Sets which delegation tools exist. `typed` is the only value: one `delegate_to_<id>` tool per peer in the MAS workflow. |

#### Not used yet

`share_reasoning` is the only field here that the runtime ignores.

---

### Delegation

Delegation means one agent hands a task to a peer through a tool call. In a MAS, an agent whose workflow node lists `delegates_to: [a, b]` gets one tool per peer: `delegate_to_a` and `delegate_to_b`.

- The runtime fixes peer IDs when it composes the MAS. The model cannot call agents outside the topology (the team shape).
- Each tool takes a `task` and an optional `context_id`.
- The peer's `spec.description` is the tool description the delegating model sees. Write it from the delegator's point of view.
- `spec.working_memory` decides whether the peer remembers earlier delegations.

Topology and dispatch modes: [mas.md](mas.md#workflow).

---

### GovernanceBinding

_Used by:_ `spec.governance`

An ordered plugin chain. BLOCK stops it and ALLOW continues. Full page: [governance.md](governance.md). Retry, circuit-breaker, and `error_policy` defaults: [reliability.md](../references/reliability.md).

Each entry is a bare plugin id or a single-key config object. `error_recovery_plugin` is YAML shorthand that installs that plugin on the ingress chain (one classifier). `error_policy` is that plugin's config. The kernel only applies the `GovernanceAction` the plugin returns.

```yaml
governance:
  - gov_no_undeclared_tool
  - retry_on_error:
      error_recovery_plugin: retry_on_error
      error_policy:
        transient: retry
        fatal: block
```

---

### LlmBinding

_Used by:_ `spec.llm`

**Deprecated** compatibility block for engine overrides. Prefer `spec.models[]`. `llm` remains so existing overlays can still patch provider and sampling hints.

| Field | Type | Description |
| ------- | ------ | ------------- |
| `model` | `string` | LiteLLM-style model string override. |
| `provider` | `string` | Engine provider hint (`mock`, `openai`, `azure`, …). |
| `temperature` | `number` [0–2] | Sampling temperature override. |
| `max_tokens` | `integer` ≥ 1 | Maximum output tokens override. |
| `reasoning`, `reasoning_effort`, `extra` | — | Same meaning as on a [`ModelEntry`](#modelentry). |

`llm` also accepts every sampling field that [`ModelEntry`](#modelentry) lists.

**Example:**

```yaml
llm:
  provider: mock
```

### Budget

_Used by:_ `spec.budget`

Call-count ceilings that the runtime engine (`LiveLlmEngine`, `BudgetTracker`) enforces during a run. Omit a field, or the whole object, for no limit. That is the default.
Leave it unset in the agent manifest and set it through an overlay instead (see [`with-hardened`](../references/reliability.md#with-hardened)). `spec.control.budget` is different: it covers token and cost accounting, and the engine does not read it yet (see [`ControlBinding`](#controlbinding)).

| Field | Type | Required | Default | Description |
| ------- | ------ | ---------- | --------- | ------------- |
| `max_llm_calls` | `integer` ≥ 0 | no | unset | Maximum LLM calls allowed in a run. |
| `max_tool_calls` | `integer` ≥ 0 | no | unset | Maximum tool calls allowed in a run. |

**Example:**

```yaml
budget:
  max_llm_calls: 50
  max_tool_calls: 100
```

### ControlBinding

_Used by:_ `spec.control`

Control-plane knobs:

- `retry` is applied by the runtime reliability layer (LLM HTTP and tool dispatch).
- `circuit_breaker` loads a library plugin (default `threshold`).
- `budget` and `rate_limiter` are accepted by the schema, but no shipped plugin enforces them in this release. Use [`spec.budget`](#budget) for call ceilings.

Set a key to `null` to disable it. Full tables: [reliability.md](../references/reliability.md).

| Key | Fields | Description |
| ----- | -------- | ------------- |
| `budget` | `max_tokens: integer`, `max_cost_usd: number` | Planned token or cost budget. Not used yet. |
| `retry` | `llm` / `tools`: `max_attempts`, `backoff_s`, `backoff_multiplier`, `jitter`, `retry_on`, `require_idempotent`, `max_backoff_s` | Infra re-issue budget. LLM default 4 attempts on `transient`+`unavailable` (429/5xx, timeout, connect-refused); tools 2 attempts on `transient`+`unavailable` when idempotent. |
| `circuit_breaker` | `plugin`, `enabled`, `failure_threshold`, `reset_timeout_s`, `on` | Off unless this key is present. Loads the `threshold` circuit-breaker plugin. |
| `rate_limiter` | `requests_per_minute: integer` | Planned LLM call-rate limit. Not used yet. |

#### Not used yet

`control.budget` and `control.rate_limiter` are accepted by the schema. No shipped plugin enforces them.

Failure classes used by `retry_on` and `on`: `transient`, `unavailable`,
`application`, `fatal` ([reliability.md § Failure classes](../references/reliability.md#failure-classes)).

**Example:**

```yaml
control:
  budget:
    max_tokens: 50000
  retry:
    llm:
      max_attempts: 4
      retry_on: [transient, unavailable]
  circuit_breaker:
    failure_threshold: 5
    reset_timeout_s: 30
    "on": [unavailable]
  rate_limiter:
    requests_per_minute: 30
```

`error_policy` and `error_recovery_plugin` live on a governance stanza, not under `control`. See [reliability.md](../references/reliability.md#specgovernanceerror_policy-error_recovery_plugin).

---

### ObservabilityBinding

_Used by:_ `spec.observability`

An ordered list of observability sink plugin ids. Every sink sees every event: it is a sequence, not a chain. Two sinks ship, and the schema accepts only these ids:

- `native` writes `events.jsonl` (from `mas-library-standard`).
- `otel` exports OpenTelemetry spans (from `mas-library-telemetry`).

Each entry is a bare plugin id or a single-key config object:

```yaml
observability:
  - native                                 # bare id — defaults
  - native:                               # with config
      path: ./traces
      events_file: events.jsonl
  - otel:
      output_path: ./otel-traces
      otel_file: spans.json
```

| Sink | Option | Description |
|------|--------|-------------|
| `native` | `path`, `events_file` | Output directory and file name for `events.jsonl`. |
| `native` | `categories` | Filters which record categories (`control`, `tool`, `model`, `execution`) are written: a list of prefixes, or `{include, exclude, enable, disable}`. Sinks still receive every event. |
| `otel` | `output_path`, `otel_file` | Where span JSONL is written (default next to `events.jsonl`). |
| `otel` | `otlp_endpoint_env` | Environment variable holding the OTLP endpoint (default `OTEL_EXPORTER_OTLP_ENDPOINT`); unset means file export only. |
| `otel` | `app_name` / `mas_id` | Declared MAS name, used as the service identity (not a path). `service_name` is ignored. |
| `otel` | `converter_profile`, `realtime`, `extensions` | Span conversion profile (default `observe_sdk`), live incremental export (default `false`), and MAS span extensions. |

Unknown plugin ids fail at load time. `spec.observability` defaults to empty. The flavour or CLI usually attaches `native`. Walkthrough: [Tutorial 8](../tutorials/08-telemetry/README.md).

---

### Debug

_Used by:_ `spec.debug`

Script configuration for the `debug_script` runtime plugin (alias `gdb`). The script is a gdb-like list of breakpoints on tool calls and results, each with commands such as `checkpoint`. Breakpoints observe and never block.
Enable the plugin from workspace `config.yaml` `plugins:` or lab `enable_plugins`. This block only supplies the script. The CLI equivalent is `mas-ctl chat --debug-script`. Overlays cannot patch it.

| Field | Type | Description |
|-------|------|-------------|
| `script_file` | `string` | Path to a debug script file. |
| `script` | `string` | Inline debug script. |

See [Tutorial 13](../tutorials/13-control-and-debug/README.md).

---

## Schema source

```bash
# From installed package
python -c "from mas.lab.schemas.paths import runtime_schema_dir; print(runtime_schema_dir() / 'agent.schema.yaml')"

# From Web UI / controller (default port 8090)
curl http://localhost:8090/api/schemas/agent
```

---

## See also

- [Governance](governance.md) — chain and plugins (`spec.governance`)
- [Reliability](../references/reliability.md) — retries, circuit breaker, defaults, logging
- [MAS manifest](mas.md) — topology, transport, and delegation
- [Overlay manifest](overlay.md) — overrides
- [Tutorial: building an agent](../tutorials/01-building-an-agent/README.md)
- [Design patterns](#designpattern): `spec.design_pattern` on agents
