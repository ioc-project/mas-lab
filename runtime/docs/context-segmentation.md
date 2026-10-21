<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Context segmentation

This page explains how the runtime assembles prompt context, tracks where each piece came from (provenance), and records it in `events.jsonl` traces.
It is for plugin authors who contribute context and for analysts who attribute prompt content to plugins.

List the segment and token counts of each assembled prompt:

```bash
jq -c 'select(.kind=="context_assembled") | {agent_id, segments, total_tokens}' traces/events.jsonl
```

## Layers

| Layer | Holds | Persistence |
|-------|-------|-------------|
| **L1: Transcript** | User/assistant turns visible to the model | Session + trace |
| **L2: Stores** | Memory, RAG, skills (via `ContextContract`) | Backend-dependent |
| **L3: DP state** | Design-pattern phase (plan/act/synth, tool queue) | `QProduct.dp_data` |

The kernel assembles L1 and L2 into the messages array for each `LLM_CALL`. L3 influences scheduling through `evaluate_next` and `handle_event`. It reaches the transcript only if the design pattern (DP) injects instruction parts.

## ContextPart and provenance

Each contributor returns `ContextPart` values with:

- **role**: system, user-facing instruction, tool catalog facet, and so on.
- **mechanism**: `inject`, `rag`, `tool_call`, `dp_instruction`, ...
- **provenance**: source plugin, segment id, optional budget weight.

The assembler merges parts, applies `ContextManagerContract.manage_history`, then enforces the token budget by evicting the lowest-priority segments first.

## Trace events

Context assembly emits observability events that benchmark pipelines consume:

| Event kind | Meaning |
|------------|---------|
| `context_assembled` | Final messages + segment count and token estimate for one LLM call |
| `context_part_contributed` | One event per contributed (retained or evicted) context part, with source and provenance |
| `llm_call_start` / `llm_call_end` | Model egress interval |
| `tool_call_start` / `tool_call_end` | Tool egress interval |

Segments in `context_assembled` align with provenance ids, so figure steps (`extract_trace_stats` and the lab figure steps) can attribute context to plugins.

See [trajectory-schema.md](trajectory-schema.md) for the full schema.

## Post-tool context

After a tool returns, the kernel may run a post-tool collect pass, so memory and DP plugins append results before the next `LLM_CALL`. Provenance tags these parts with mechanism `tool_call`.

## Budget eviction

When estimated tokens exceed the manifest budget:

1. Trim history via context manager (summarize or drop oldest turns).
2. Drop optional context parts by priority (skills before system policy).
3. Pinned parts (`ContextPart.pinned`) are not evicted by the budget strategy.

Configure budgets in manifest `spec.context`. [docs/references/context-management.md](../../docs/references/context-management.md) covers the request layout, pinned state, and budget derivation. [docs/manifests/context-assembly.md](../../docs/manifests/context-assembly.md) covers the manifest keys.

## Related docs

- [dev/contracts/state-and-context.md](dev/contracts/state-and-context.md)
- [automaton-product-model.md](automaton-product-model.md) (§6 and §7)
- [mealy-envelope.md](mealy-envelope.md) : context assembly order in the checklist
