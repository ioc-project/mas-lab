<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# State and Context Contracts

These contracts cover prompt context assembly and conversation-history trimming.
Session persistence uses kernel `Session` / `SessionState`, not a
`SessionContract` class.

See also: [taxonomy.md](taxonomy.md) · [model-and-tools.md](model-and-tools.md) ·
[context-segmentation.md](../../context-segmentation.md)

---

## ContextContract

Typed prompt context contribution — the primary extension point for RAG,
memory bridges, skills, and DP instructions.

| Method | Purpose |
|--------|---------|
| `collect_context(request)` | Return `ContextPart` list with provenance |

**Kernel path:** context assembly runs before each scheduled `LLM_CALL`; parts
are merged, history-filtered, and token-trimmed to the model context window
minus completion reserve (overridable via `spec.context_manager.params.trimmer`).
See
[context-segmentation.md](../../context-segmentation.md) and
[context-assembly.md](../../../../docs/manifests/context-assembly.md).

`context_manager` plugins live in `library-standard` (`plugins/context/conversation.py`).
They implement `ContextManagerContract`: return a protocol-valid `messages[]`
prefix from `manage_history` (user-turn atomicity, tool ask then matching
results). The two layers and the rules are on
[ContextManagerContract](#contextmanagercontract). Token trim uses a shared
helper in `library-standard` (`trim_messages_to_budget`), not a registry
plugin type.

---

## ContextManagerContract

Supporting interface for conversation-history trimming and summarization. It is not a separate product factor in
the Mealy model. Python class: `mas.runtime.contracts.context_manager_contract.ContextManagerContract`.
Implementations live in `library-standard`; this section is the interface they must satisfy.

`ContextContract` contributes prompt parts. This contract decides how much of the committed conversation is
resent on the next LLM call.

### Two layers of an LLM call

A provider call has two layers:

| Layer | What it is | What it inspects |
|-------|------------|------------------|
| **1. Protocol** | The HTTP body: a typed `messages[]` list (`role`, `tool_calls[].id`, `tool_call_id`, …) | Structure: roles, ids, counts. The body of a tool result (`"12°C"`) is opaque. |
| **2. Tokens** | A chat template flattens those objects into one token stream, then the weights run | Ordinary next-token prediction in the tool-result *slot* of the template |

Layer 1 runs before tokenization. A 400 for mismatched tool results is a failed protocol check, not the model
misreading the transcript. LiteLLM translating to Anthropic or Bedrock is still layer 1: it counts `toolUse`
blocks against following `toolResult` blocks.

The HTTP API is stateless: continuity is only the `messages[]` the caller resends. If call 1 returned "call
tools A and B", call 2 must include that assistant message and then the two matching results.

Call 1, sent:

```text
user: Weather in Paris and London?
```

Call 1, returned by the model (the vendor does not store it):

```text
assistant: tool_calls = [
  {id: call_1, get_weather, Paris},
  {id: call_2, get_weather, London},
]
```

Call 2, sent (previous transcript, the ask, both results):

```text
user: Weather in Paris and London?
assistant: tool_calls = [call_1, call_2]
tool: tool_call_id=call_1  "12°C, cloudy"
tool: tool_call_id=call_2  "8°C, rain"
```

### Rules `manage_history` must stick to

`manage_history(past, budget_tokens)` sees committed history only. The runtime appends the current user turn and
in-turn working memory after this method. The return value is layer-1 objects, not a token string.

1. User-turn atomicity. Slice on `role=user`. A turn is that user message plus everything until the next user
   message (assistant tool asks, `role=tool` results, follow-up assistant text). Never drop a tool ask and keep
   its results, or the reverse.
2. Ask, then results, then anyone else. After an assistant message with N `tool_calls`, the next N messages
   must be `role=tool` for those ids, with nothing in between.
3. Do not compact the live tool round. It is working memory, not `past`. Summarize or drop only older user
   turns (`keep_turns` stay verbatim). Cache a summary; do not re-summarize on later calls until the managed
   payload exceeds the budget including `hysteresis_ratio`.
4. Pair on ids. Match `tool_calls[].id` with `tool_call_id`. Never parse tool-result content to decide what to keep.

The kernel does not repair pairing. After the `spec.assembler` plugin returns, it asserts the invariant and raises
`ProviderPayloadError` (survives `python -O`) if it fails. The bundled `ContextAssemblerPlugin` runs the last-pass
repair (`sanitize_provider_messages`: fills missing results with empty strings, drops extras). A custom assembler
plugin must return an already-paired payload or call `sanitize_provider_messages` itself.

| Method | Parameters | Notes |
|--------|------------|-------|
| `manage_history(past, budget_tokens)` | committed messages; token hint (model context window minus completion reserve) | Return a possibly shorter `past`. Must remain a valid layer-1 prefix. |

**Runtime path:** `assemble_llm_messages` (kernel) → `spec.assembler` plugin
(default `ContextAssemblerPlugin`: `CMFactory` / cached context manager →
`manage_history` → append current user → pin-tail working memory → trim →
sanitize) → kernel asserts pairing (`assert_provider_payload`) → provider.
After each turn, kernel `compact_committed_history` asks the same plugin to
rewrite stored history to the recency cap.
Manifest: `spec.context_manager`, `spec.assembler` (see
[context-assembly.md](../../../../docs/manifests/context-assembly.md)).

Bundled implementations (not the contract): `StackConversation`,
`SlidingWindowConversation`, `SummarizingConversation` in `library-standard`.

---

## Implementation status

| Contract | Kernel integration | Typical plugin location |
|----------|-------------------|-------------------------|
| `context` | Hot path (every LLM egress) | `library-standard` |
| `context_manager` | Hot path | `library-standard` |
| `session` | Turn boundaries | flavour-dependent |
| `execution` | Checkpoint commands | ctl checkpoint CLI |
| `shared_context` | MAS workflows | lab / sample apps |

Contract classes are declared in `mas.runtime.contracts.base`; concrete plugins
register via `plugin_id` and manifest `spec.plugins`.
