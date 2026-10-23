<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
Status: Implemented. Authoritative user docs:
[context-assembly.md](../manifests/context-assembly.md),
[agent.md](../manifests/agent.md), [plugin-bindings.md](../manifests/plugin-bindings.md).

# Working memory: what it is, and unifying its compaction story

*Working memory* is the conversation buffer an agent builds during a run. *Compaction* shrinks old history so it fits the model's context window. This note explains how the two relate and why one config surface controls compaction.

Compaction is **context assembly**, not a working-memory cache. `spec.working_memory.compaction` writes `spec.context_manager` when that slot is omitted. The summarising context manager's hysteresis is the cache. It reuses the existing summary until the managed history exceeds the budget by `hysteresis_ratio` (default 0.1, that is 10%).

## What working memory is: one buffer, two phases

Each agent has one accumulating conversation buffer, not two. Think of it as two phases of one pipeline.

1. **In-flight (uncommitted) phase.** `WorkingMemoryStore` (`runtime/src/mas/runtime/boundary/context/working_memory.py`) holds it. While a turn's ReAct loop runs, every tool call and tool result of THIS turn is appended here. The model needs to see its own uncommitted tool trajectory before the turn ends.
2. **Committed phase.** `AutoCtxAssembler.committed_messages` and `turn_history` (`runtime/src/mas/runtime/driver/mocks.py`) hold it. At the end of each turn, `note_agent_response()` copies the in-flight buffer into `committed_messages`, then clears the in-flight buffer. It then applies the context manager's recency cap, so the stored log cannot grow without bound.

A model request therefore has three pieces, in this order:

1. The system prompt (`injected_context`). It is rebuilt from the manifest every time and never accumulates.
2. `committed_messages`: the recency-capped record of recent user turns, tool calls, tool results, and assistant replies.
3. Whatever the current turn has produced but not yet committed.

`assemble_llm_messages()` stitches these three pieces into the message list sent to the LLM.

`spec.working_memory.persistent` governs the committed phase only. A delegated agent falls back on it across separate `delegate_to_<agent>` calls. `WorkingMemoryRegistry` keys it by `(session_id or context_id, agent_id)` (`runtime/src/mas/runtime/boundary/context/working_memory_registry.py`).

`_finalize_turn` (`ctl/src/mas/ctl/session/controller.py`) folds the in-flight buffer into `committed_messages` whenever there is a response or working memory to fold. This includes a turn paused for HITL (human in the loop), for example an approval gate on a tool call. Already-executed tool calls are therefore not lost when the next turn's `note_user_input()` clears working memory. Checkpointing still happens only for a turn that completed. See `ctl/tests/test_session_controller_finalize_turn.py`.

## The problem: two overlapping compaction surfaces

`docs/schemas/runtime/agent.schema.yaml` once had two places that both claimed to control how much history is kept.

1. **`spec.context_manager.params`**: `max_turns` or `window_size`, `max_messages`, `summary_threshold`, `keep_turns` (`docs/schemas/runtime/fragments/context-manager-params.schema.yaml`). This surface is real. `CMFactory.create(manifest=manifest)` (`runtime/src/mas/runtime/contracts/cm_factory.py`) resolves it through the plugin registry to one of three working, tested strategies in `library-standard/src/mas/library/standard/plugins/context/conversation.py`:
    - `StackConversation` caps total messages.
    - `SlidingWindowConversation` caps by turn-pair count.
    - `SummarizingConversation` compresses older turns into a summary system block and keeps the last N verbatim.

    The assembler plugin calls `cm.manage_history(past, budget_hint)` on **every turn**. `past` is the committed history. `budget_hint` is the model context window minus the completion reserve. `spec.context_manager.params.trimmer` can override it. [context-assembly.md](../manifests/context-assembly.md) covers the assembly-time token trim (tool-group-aware, with the live working-memory round pinned at the tail).
2. **`spec.memory.compaction`** (formerly in `agent.schema.yaml`): a richer, schema-only surface. It offered `strategy: keep_recent|summarize|chunked_summarize`, `keep_recent_ratio`, `min_chunk_ratio`, `safety_margin`, `identifier_preservation`, and `summarize_instructions`. **Nothing in the codebase read it.** A repo-wide grep for `chunked_summarize` and for any consumer of `spec.memory.compaction` found only the schema file.

Two surfaces claimed the same job, and only one worked. [flavour-boundary.md](flavour-boundary.md) calls out the same kind of drift in other subsystems.

`SummarizingConversation` needs no summarizer to run. `summarize_fn` is optional. With a `drop` summarizer, or `llm` with no live engine, it keeps the last `keep_turns` user turns verbatim and discards older ones.

## Decision: one config surface, `spec.context_manager`

`spec.working_memory.compaction` is sugar that writes that slot. Compaction policy is `manage_history`. The LLM view is bounded at assemble time. After each turn commit, the same recency cap is written back to the committed log, and the folded prefix is dropped. The cache for in-turn LLM calls is **hysteresis on the context-manager instance**.

1. **`working_memory.compaction` translates to `context_manager`:**

    ```yaml
    working_memory:
      persistent: true
      compaction:
        strategy: keep_recent   # keep_recent | sliding_window | summarize
        max_messages: 200       # keep_recent → stack
        window_size: 20         # sliding_window
        summary_threshold: 0    # summarize: 0 uses model context_window − reserve
        keep_turns: 10          # summarize: recent user turns kept verbatim
    ```

    Mapping: `keep_recent` to `stack`, `sliding_window` to `sliding_window`, `summarize` to `summarising`. Prefer writing `spec.context_manager` directly. If both are set, `context_manager` wins.

2. **Summarizer sub-plugins** (registry type `summarizer`):
    - `llm` (default) uses the agent's engine through `CMFactory.create(..., engine=ctx.engine)`.
    - An omitted or `any` `params.model` uses the summarizer slot (Agent, then MAS, then `experiment.models.summarizer`), else the turn model.
    - An optional `params.model` (a `spec.models[].id` or a LiteLLM string) uses a different model for the summary call.
    - An optional `params.instructions` replaces the package system prompt. The **trigger** is still the token budget.
    - `drop` discards older turns.
    - The spec does **not** store the engine.

    See [summarization.md](../manifests/summarization.md). Example: [summarizer-override](https://github.com/ioc-project/mas-lab/tree/main/library-standard/examples/context/summarizer-override).

3. **Package default** when both slots are omitted: `context_manager: summarising` with `summarizer: llm` (see [plugin-bindings.md](../manifests/plugin-bindings.md)). This is not unbounded stack history.

4. **`spec.memory.compaction` is deleted.** Nothing wired it.

Tests:

- `library-standard/tests/test_working_memory_compaction.py`: facade translation, `build_llm_summarize_fn`, and real `CMFactory` instantiation.
- `ctl/tests/test_working_memory_compaction_bootstrap.py`: end to end through `instantiate_runtime()`.

## Non-goals

- Working memory that persists across processes or restarts (`spec.memory.persistence`). This is a separate concern.
- Compacting the semantic and episodic retrieval-store memory layers (`spec.memory.types[]`). That is a different subsystem (`MemoryContract`) with its own lifecycle.
- `chunked_summarize`, `identifier_preservation`, and `safety_margin` from the old `memory.compaction` shape. No evidence of demand beyond the unimplemented schema.

## See also

- [Context management reference](../references/context-management.md)
- [Context assembly](../manifests/context-assembly.md)
- [Conversation summarization](../manifests/summarization.md)
- [Compiled defaults](../references/defaults.md)
