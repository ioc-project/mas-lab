<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin bindings in manifests

A **plugin binding** is how an Agent manifest names a plugin and passes it parameters.
A *plugin* is a class that a library manifest (`library.yaml`) registers under a type. The slot name is that type.
Use a binding to pick a non-default plugin for `design_pattern`, `context_manager`, `assembler`, `hitl_contract`, or `user_io_contract`. Use it also for the list slots `governance`, `observability`, and `context_sources`.
[plugins-reference.md](../plugins-reference.md) lists the registered plugins.

Minimal working bindings, in shorthand:

```yaml
spec:
  design_pattern: cot
  governance:
    - gov_no_undeclared_tool
```

Schema: `docs/schemas/runtime/agent.schema.yaml`. Code: `mas.runtime.spec.plugin_binding.normalize_plugin_binding`.

Three forms behave the same at run time: omit the slot, write the plugin name, or write the full object with schema defaults.
`mas-ctl compile` prints the full object so you can diff it. [Compiled agent defaults](../references/defaults.md) shows Tutorial 1's minimal manifest, compiled.

---

## Two shapes

### 1. Singleton slot (one plugin)

The spec key is the role (`design_pattern`, `context_manager`, `assembler`). The value is the plugin name, optionally with parameters.
String shorthand and object form are equivalent:

```yaml
spec:
  design_pattern: cot          # same as {type: cot}
  context_manager: summarising
  assembler: assembler
```

Spec keys have no `_plugin` suffix. Registry type names match the slot. A sub-plugin (today only `context_manager.params.summarizer`) uses the same shorthand: `summarizer: llm` is the same as `{type: llm}`.

`params` holds constructor and kernel keyword arguments for **that** plugin.
`summarising` rejects `max_messages`; `stack` rejects `summarizer`. Assembly
knobs (`trimmer`, `working_memory_messages`) are shared. An unknown plugin id
keeps extra keys so a third-party context manager can carry its own kwargs.
`design_pattern` also accepts `config` as an alias.
`type` and `ref` use the same lookup: a registered name, an alias (`full-history`, `tot`, ...), or the full plugin URN (`mas.cm.stack`). Write one of them, not both.
File paths, `module://` and `oci://` locators are not resolved in this release. To use your own class, register it in a library manifest (see [Libraries](../libraries.md)) and name it here.

### 2. List slot (several plugins)

The spec key is still the role. Each list item is a plugin name, either bare or as a one-key map of parameters:

```yaml
spec:
  governance:
    - sample_governance:
        hitl_on_tool: true
        hitl_mode: interactive
    - no_undeclared_tool
    - retry_on_error:
        error_recovery_plugin: retry_on_error
        error_policy:
          transient: retry
          fatal: block
  observability:
    - native
```

`error_recovery_plugin` and `error_policy` may appear on any governance stanza. The parser flattens them and installs the recovery plugin on the ingress chain, not on `KernelConfig`.
Full chain: [governance.md](governance.md). Retry defaults: [reliability.md](../references/reliability.md).

Every list slot defaults to empty (no plugins). The flavour or CLI attaches native observability. Omitting `spec.observability` does not attach it.

---

## Package defaults and equivalent explicit config

Omitted fields come from `defaults.yaml`. A workspace `config.yaml` `defaults:` block can override them. Compile writes the object form below.

### `design_pattern` (default `react`)

Omit the field, write `design_pattern: react`, or:

```yaml
spec:
  design_pattern:
    type: react                # aliases: react@v1
    params:
      max_steps: null          # kernel dispatch cap; null/omitted = no cap
      max_cot_pass: 1          # extra CoT refinement passes
      parallel: true           # parallel tool calls
```

`design_pattern: cot` has the same shape with `type: cot` and the same default params.
Other shipped ids: `single_pass`, `introspection`, `plan_execute`, `tree_of_thoughts`, `deterministic_single`, `deterministic_linear`, `deterministic_parallel`.
Aliases: `analyst` and `planner` (to `react`), `linear` (to `single_pass`), `reflection` (to `introspection`), `tot` (to `tree_of_thoughts`), `plan-execute` (to `plan_execute`).

| Param | Maps to | Default | Who uses it |
|-------|---------|---------|-------------|
| `max_steps` | `KernelConfig.max_auto_steps` | none | every pattern (loop cap; `with-hardened` sets 512) |
| `max_cot_pass` | `KernelConfig.max_cot_pass` | 1 | `cot`, `introspection`, `tree_of_thoughts` (min 2 internally) |
| `parallel` | `KernelConfig.parallel_tool_calls` | true | `react` and tool-calling patterns |

### `assembler` (default `assembler`)

```yaml
spec:
  assembler:
    type: assembler            # aliases: context-assembler, ctx-assembler
    params:
      emit_segments: true
      always_reassemble: false
      # token_budget: omit = no part-eviction budget
```

The assembler builds the LLM `messages[]` from context. One assembler ships (`ContextAssemblerPlugin`). History policy is not an assembler param. It lives on `spec.context_manager`.

### `context_manager` (default `summarising`)

The context manager keeps conversation history within the model's context window (the maximum input size). Each manager is a plugin that carries its own strategy code.

```yaml
spec:
  context_manager:
    type: summarising
    params:
      keep_turns: 10
      hysteresis_ratio: 0.1
      summarizer: llm          # sub-plugin; ≡ {type: llm}. Alternative: drop
      # summarizer: {type: llm, params: {model: gpt-4o-mini}}  # cheaper summary LLM
      working_memory_messages: 20
      trimmer:
        reserve_tokens: 2000   # effective completion budget
        safety_margin_ratio: 0.1
```

Equivalent forms of the other managers:

- `sliding-window`: `{type: sliding-window, params: {keep_turns: 10}}`. It drops older turns and uses no summarizer.
- `stack`: `{type: stack}`, with optional `max_messages`.

Full examples: [context-assembly.md](context-assembly.md).

### `summarizer` (sub-plugin of `summarising`)

The summarizer compacts history: it replaces older turns with a shorter summary. It is not a top-level spec key. The `summarising` context manager composes it, and its registry type is `summarizer`.

| Plugin | Default? | Behaviour |
|--------|----------|-----------|
| `llm` | yes | Uses the summarizer model slot (Agent, then MAS, then `experiment.models.summarizer`), else the agent's resolved turn model (`summarize_messages`). Optional `params.model` (a `spec.models[].id` or LiteLLM string) picks another model. Degrades to `drop` without a live engine. |
| `drop` | | Discards older turns and keeps `keep_turns` verbatim. |

```yaml
# default (inside summarising) — summarizer slot else this agent's turn model
params:
  summarizer: llm              # ≡ {type: llm}

# cheaper / dedicated summarizer
params:
  summarizer:
    type: llm
    params:
      model: gpt-4o-mini       # or a spec.models[].id such as summarizer
      # instructions: |        # optional; default is the package system prompt
      #   Preserve city names and dates.

# explicit drop
params:
  summarizer: drop             # ≡ {type: drop}
```

Thresholds, prompt, logs, MCE judge: [summarization.md](summarization.md).
Example: [summarizer-override](https://github.com/ioc-project/mas-lab/tree/main/library-standard/examples/context/summarizer-override).
Card: [summarizer.md](https://github.com/ioc-project/mas-lab/blob/main/library-standard/src/mas/library/standard/plugins/context/summarizer.md).

### `memory`

`memory` is not a plugin binding. The schema accepts `memory: semantic` (string), a list of names, or the object form `memory.types[]` (see [agent.md](agent.md#memoryconfig)). The runtime does not read `spec.memory` in this release. Memory works through `spec.memory_seed` and the `memory-search` tool instead.

### List plugins

| Slot | Default | Shipped plugins |
|------|---------|-----------------|
| `governance` | `[]` | `sample` / `sample_governance`, `gov_no_undeclared_tool` (alias `no_undeclared_tool`), `retry_on_error`, `backtrack_on_error` |
| `observability` | `[]` (flavour/CLI may attach `native`) | `native`, `otel` (from `mas-library-telemetry`) |
| `context_sources` | `[]` | `native`, `adk`, `langchain` (skill engines from `mas-library-skills`) |

Tool providers are not an agent slot. The `local` provider loads `spec.tools`. Remote MCP endpoints come from infra [`ToolServerRegistry`](infra.md#toolserverregistry). Agent and overlay schemas reject a `providers` key.

---

## Compaction

Compaction shortens history so it fits the context window. `spec.working_memory.compaction` is shorthand for `spec.context_manager`. Prefer `spec.context_manager`. If both are set, `context_manager` wins.

The context manager owns recency. `manage_history` bounds the LLM view. After each turn commit, the same cap is written back to `committed_messages` and conversation chunks, and folded prefix data is dropped. Hysteresis on the context-manager instance avoids a summarizer LLM call on every in-turn step.

`compaction.model` (strategy `summarize`) is shorthand for `summarizer.params.model`. See [summarization.md](summarization.md).

`stack` with no `max_messages` has no recency cap. Set `max_messages`, or use the default `summarising` or `sliding-window` plugin.

---

## Overlay merge

An [overlay](overlay.md) is a YAML patch merged onto a manifest. Singleton plugin slots (`design_pattern`, `context_manager`, `assembler`) merge with `plugin_binding_merge`:

- A string overlay (`context_manager: stack`) replaces the binding.
- The same `type` or `ref` (or a params-only patch) deep-merges `params`. Nested maps keep sibling keys: patching `trimmer.reserve_tokens` does not drop `trimmer.max_tokens`.
- A changed `type` or `ref` replaces the binding. Constructor kwargs from the previous plugin are dropped.

On `design_pattern`, `params` wins over the legacy `config` alias when both are present.

```yaml
# base
spec:
  context_manager:
    type: summarising
    params: {keep_turns: 10}

# overlay — type change drops keep_turns
spec:
  context_manager:
    type: stack
# result: {type: stack}

# overlay — same type keeps and patches params
spec:
  context_manager:
    params: {hysteresis_ratio: 0.3}
# result: {type: summarising, params: {keep_turns: 10, hysteresis_ratio: 0.3}}

# overlay — nested params keep siblings
spec:
  context_manager:
    params:
      trimmer: {reserve_tokens: 4000}
# result trimmer: {max_tokens: 128000, reserve_tokens: 4000}  (if base had both)
```

### Malformed slots

A slot that is not a plugin name or a `{type, ref, params}` object is a hard error (`PluginBindingError`) at `mas-ctl compile` and `mas-ctl validate`. This holds even with `--no-validate`.

The runtime reads an already-compiled manifest leniently (`normalize_plugin_binding_lenient`). It treats a malformed value as omitted and uses the slot's platform default. This only matters if you build a manifest dict by hand, bypassing `mas-ctl`.

---

## See also

- [Compiled agent defaults](../references/defaults.md) — Tutorial 1's minimal manifest, fully expanded
- [agent.md](agent.md) — every agent spec field
- [context-assembly.md](context-assembly.md) — assembler, context managers, summarizers
- [summarization.md](summarization.md) — thresholds, summarizer/judge model overrides, logs
- [runtime/docs/agent-defaults.md](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/agent-defaults.md)
- [plugins-reference.md](../plugins-reference.md) — catalog of registered plugins
