<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Context assembly

Context assembly decides which messages each LLM call receives.
Configure it on an Agent manifest with two fields:

- `spec.assembler` builds the message list.
- `spec.context_manager` trims or summarizes the history.

Change them when conversations outgrow the model's **context window** (the maximum tokens the model reads in one request) or when you need to control what the model sees.
The defaults need no configuration.

Both lines below restate the defaults:

```yaml
spec:
  assembler: assembler
  context_manager: summarising
```

Code: `ContextAssemblerPlugin.assemble_messages` and kernel dispatcher `mas.runtime.boundary.context.assemble`.
Schema: `agent.schema.yaml`.
Bindings: [plugin-bindings.md](plugin-bindings.md).
For request composition, pinning, resolved model limits, context snapshots, cache metrics, and cost accounting, see the [context-management reference](../references/context-management.md).

## Contents

- [`assembler`](#assembler-required-default)
- [`context_manager` plugins](#context_manager-plugins)
- [Assembly token trim](#assembly-token-trim-model-context-window)
- [Working memory count cap](#working-memory-count-cap)
- [Tool-call pairing](#tool-call-pairing)
- [See also](#see-also)

## Message order

Each LLM call builds `messages[]` in a fixed order:

1. System band: injected context, memory seeds, and `collect_context` parts.
2. **Committed history**, after `ContextManagerContract.manage_history`.
3. The current user turn (`last_user_text`).
4. **In-turn working memory**: the assistant and tool rows from the dispatch loop.

`context_manager` plugins slice committed history by **user turn**.
After the runtime appends working memory, assembly runs one pairing pass (one result per tool call).
See [Tool-call pairing](#tool-call-pairing).

Compaction (shrinking history so it fits the window) does not mutate in-turn working memory.
`manage_history` bounds the LLM view.
After each turn commit, the runtime writes the same cap back to the stored log.
The summarising plugin caches the last summary (**hysteresis**), so the summariser LLM is not called on every in-turn step.

## `assembler` (required default)

The assembler builds `messages[]`.
Omit the field, write `assembler: assembler`, or write the object.
All three behave the same.

```yaml
# default (all three equivalent)
#   (omit)
#   assembler: assembler
spec:
  assembler:
    type: assembler            # alias: context-assembler → mas.ctx.assembler
    params:
      emit_segments: true      # default
      always_reassemble: false # default
      # token_budget: omit = no part-eviction budget
```

One assembler ships (`ContextAssemblerPlugin`).
History policy is not an assembler param.
It belongs to `spec.context_manager`.

## `context_manager` plugins

Each manager is a plugin with strategy code.
The summarising manager also composes a **summarizer sub-plugin** (registry type `summarizer`: `llm` or `drop`).
A summariser is the component that compresses older turns into a short text.

The runtime rebuilds system context parts for each LLM request.
Skills and other `ContextPart`s marked `pinned` stay outside conversation summarization.
Tool schemas are sent separately and also count toward the request budget.
The [context-management reference](../references/context-management.md) gives the complete ordering and pinning rules.

The runtime caches one manager instance per `(ctx, manifest)`, so hysteresis can reuse a summary.

### `summarising` (package default)

Omit `context_manager`, write `context_manager: summarising`, or write the full form:

```yaml
spec:
  context_manager:
    type: summarising
    params:
      keep_turns: 10
      hysteresis_ratio: 0.1
      summarizer: llm            # agent's model; drop = discard older turns
      trimmer:
        reserve_tokens: 2000
        safety_margin_ratio: 0.1
```

The plugin keeps the last `keep_turns` user turns verbatim.
It then asks the summarizer to compress older turns.
`summarizer: llm` uses this agent's model.
Without a live engine it behaves like `drop`.
Hysteresis (`hysteresis_ratio`, default `0.1`) acts as the cache.
The plugin computes no new summary until the managed payload exceeds the budget times 1.1.

### `sliding-window`

This plugin drops older turns.
It has no summarizer.

```yaml
spec:
  context_manager: sliding-window   # ≡ {type: sliding-window, params: {keep_turns: 10}}
```

The explicit form:

```yaml
spec:
  context_manager:
    type: sliding-window        # alias: sliding_window
    params:
      keep_turns: 10            # aliases: window_size, max_turns
```

### Summarizer sub-plugins

Summarizer is not a top-level spec key.
Bind it only on `type: summarising`:

| Plugin | Omit / shorthand | Equivalent | Behaviour |
|--------|------------------|------------|-----------|
| `llm` (default) | `summarizer: llm` | `{type: llm}` | Summarizer slot, else the agent's live engine; degrades to drop without one |
| `drop` | `summarizer: drop` | `{type: drop}` | Discard older turns |

Use a cheaper summary model with `params.model`, a `spec.models[].id` or a LiteLLM string:

```yaml
params:
  summarizer:
    type: llm
    params:
      model: gpt-4o-mini
```

The context manager owns recency (`keep_turns`) and the hysteresis cache.
The summarizer returns only summary text, or nothing, which means drop.
The runtime binds the live engine from `ctx.engine` when it instantiates the context manager.
The spec does not store it.

### `stack` (alias `full-history`)

This plugin keeps the full history.

```yaml
spec:
  context_manager: stack
```

```yaml
spec:
  context_manager:
    type: stack                 # or full-history
    params:
      max_messages: 200         # omit = no recency cap; pairing still repaired
```

## Assembly token trim (model context window)

The runtime derives the automatic cap from the **effective model** after infra mappings.
The cap counts fixed system and context parts, the current input, working memory, and tool schemas.
It reserves completion tokens, then leaves a safety margin.

Override the cap in one of three places:

- `spec.models[].context_window`
- infra `spec.models.generation.context_window` ([infra.md](infra.md#llm-output-token-policy))
- `spec.context_manager.params.trimmer`

```yaml
spec:
  models:
    - model: gpt-4o
      max_tokens: 2000          # completion reserve
      # context_window omitted: auto-resolve from the effective model
  context_manager:
    type: summarising           # default; sliding-window | stack also valid
    params:
      keep_turns: 10            # recent user turns never summarized (override per agent)
      hysteresis_ratio: 0.1     # summary reuse stays within the hard safety budget
      trimmer:
        reserve_tokens: 2000    # defaults to the effective completion budget
        safety_margin_ratio: 0.1
```

| Field | Meaning |
|-------|---------|
| `trimmer.max_tokens` | Optional explicit input ceiling. Omit to resolve the effective model window. |
| `trimmer.token_budget` | Alias for `max_tokens` |
| `trimmer.reserve_tokens` | Completion reserve. Defaults to the effective output budget. |
| `trimmer.safety_margin_ratio` | Fraction held back for estimate error and provider overhead (default `0.1`). |
| `keep_turns` | Last N committed user turns kept verbatim (default 10). Older turns are summarized or dropped. |
| `hysteresis_ratio` | Reuse the summary until near the safety-bounded hard budget (default `0.1`). |

The runtime passes in-turn working memory as **`pin_tail`**.
It drops the oldest tool-call groups in history first.
It trims working memory by group only after history is exhausted.
This keeps the live tool round intact for the provider.

The trim implementation lives in **mas-library-standard** (`trim_messages_to_budget`).
Pairing repair, history slicing, working-memory caps, and compile defaults live under `lib/context/`.
The assembler plugin uses those helpers, and `mas-ctl compile` uses the same defaults.
The kernel does not.

The kernel raises `ProviderPayloadError` if pairing is still broken after the plugin returns, even under `python -O`.
Custom `spec.assembler` plugins must produce a paired payload.
They can call `sanitize_provider_messages`.

After each turn commit the kernel asks the assembler plugin to apply the same
recency policy to the **covering set** (`committed_messages` is the chat
projection of that set). Folded leaves stay in the archive; snapshots serialize
the full tree. Hysteresis still avoids re-summarizing every in-turn LLM call.

A `context_manager` value must be a plugin name or a `{type, ref, params}` object.
Any other value is a hard error at `mas-ctl compile` or `validate` time.
At assemble time the kernel is lenient.
It treats a malformed value that bypassed `mas-ctl` as omitted and uses the default context manager, so one bad field cannot fail every turn.
See [plugin-bindings.md](plugin-bindings.md#two-shapes).

---

## Working memory count cap (deprecated)

`working_memory_messages` is accepted and **ignored**. Live tool rows are kept
until the token trimmer (model window minus completion reserve) needs space.
See [context-management](../references/context-management.md).

### Not used yet

The schema accepts `spec.context_manager.params.working_memory_ref` and `spec.context_manager.memory`.
The shipped assembler does not read them.

## Tool-call pairing

The HTTP API is stateless.
If the model asked for tools A and B, the next request must contain that assistant message and then those two results.
Assembly runs one pairing pass after `manage_history` and working memory (one result per tool call).
The kernel then raises `ProviderPayloadError` if the payload is still unpaired.
The server checks **ids and counts**, not the text inside a tool result.

Plugin authors find the canonical rules in the [ContextManagerContract](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/dev/contracts/state-and-context.md#contextmanagercontract): two layers, protocol versus tokens, and ask-then-results.

## See also

- [Compiled agent defaults](../references/defaults.md): `assembler`, `context_manager`, and `summarizer`, fully expanded
- [plugin-bindings.md](plugin-bindings.md): string shorthand vs `{type, params}` vs list slots
- [summarization.md](summarization.md): thresholds, which model, MCE judge, logs
- [agent.md](agent.md): `working_memory` (live round plus persistent buffer; compaction is sugar)
- [ContextManagerContract](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/dev/contracts/state-and-context.md#contextmanagercontract)
- [working-memory-compaction.md](../design/working-memory-compaction.md)
- [plugins-reference.md](../plugins-reference.md)
