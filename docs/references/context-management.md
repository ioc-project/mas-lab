<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Context Management

This page describes what a model request contains, what stays pinned, and what gets summarized. Read it when a prompt grows too large or when you debug token usage.

## The problem

A model accepts a limited number of tokens. That limit is its *context window*. Each call must fit the agent role, history, tools, and the current turn into that window.

When history grows too large, the runtime summarizes old turns. This is *compaction*. *Pinned* parts are never dropped or summarized.

Example: take a request with four parts. Only the history part is summarized. The system message and the tool schemas are rebuilt on every call.

The manifest controls are in [context assembly](../manifests/context-assembly.md) and [conversation summarization](../manifests/summarization.md).

## Request Composition

The engine sends an OpenAI-compatible chat request. The message sequence has four parts, in this order:

1. A system message. It holds the agent context, memory seeds, and collected `ContextPart` sections. Sections are ordered by placement, then priority.
2. Managed committed history: an optional summary, then recent turns.
3. The current user turn.
4. In-turn working memory, including the live tool-call and result group.

Function tool schemas travel separately, in the request's `tools` field. They are not in `messages[]`. They are rebuilt for each model call and count toward the context window. They are not conversation history, and the runtime never summarizes them.

## Pinned and Summarized State

The system message is rebuilt for every model call, outside committed conversation history. Summarization therefore never rewrites the agent's role prompt or the collected system context.

`ContextPart.pinned` controls whether a context-budget strategy may evict a section.

- `ContextPart.skills()` pins the skill catalog by default.
- The native skills plugin also pins the full body of each activated skill as a `skills/activated/<name>` system part. This is the default.
- The runtime re-injects pinned parts on every call. It does not pass them to the conversation summarizer.
- The activation tool result also stays in history. The summarizer may later compress that duplicate. The pinned system copy stays until the skill is unloaded or the session ends.

Committed conversation history is the summarizer's input. The summarizing context manager keeps the latest `keep_turns` user turns verbatim and compresses older turns. It keeps tool-call and tool-result groups together.

The live working-memory tail is pinned during assembly. The trimmer may still drop its oldest complete tool groups if the fixed context leaves too little room.

`inspect_context` returns only numeric usage, cache results, and metadata such as source, section, and pinned status. It does not return prompt text or tool arguments. Full-content observability traces may still contain messages when content tracing is enabled. Treat those traces as sensitive.

## Model Window Resolution

An omitted `spec.models[].context_window` means **auto**. The runtime resolves it in this order:

1. The explicit agent model `context_window` override.
2. The infra `generation.context_window` override.
3. The model catalog entry for the model actually resolved after infra model mappings.
4. The fallback, 128,000 tokens, with a warning that names the unresolved model and the override field to use.

The built-in catalog records Gemini 2.5 Flash at 1,048,576 input tokens.

The compiler does not write a generic value back as an explicit 128,000-token model override. Infra may remap `any` or an experiment model after compile. So the runtime resolves the final window when it builds the live engine.

MAS Lab makes no network request to LiteLLM or another provider to look up context metadata at startup. Known models use the local catalog. For custom or proxy-only model names, set `context_window` on the model binding or `generation.context_window` in infra. This keeps startup deterministic and avoids an extra credentialed network dependency.

The completion reserve comes from the first of these that is set:

1. `trimmer.reserve_tokens`.
2. The effective output-token budget (`spec.models[].max_tokens`).
3. The 2,000-token model default (`DEFAULT_MODEL_MAX_TOKENS`).

You can override `max_tokens`, `context_window`, and the trimmer settings independently.

## Safety Margin and Hysteresis

The automatic budget accounts for the resolved window, completion reserve, fixed system and context content, current input, working memory, and tool schemas. It keeps a safety margin for tokenizer approximation and provider request overhead. The default `trimmer.safety_margin_ratio` is `0.1` (10% of the resolved input window).

For automatic summarization, the soft trigger sits below the hard safety-bounded history limit by `1 + hysteresis_ratio`. With the default `hysteresis_ratio: 0.1`, the summarizer can reuse the cached summary as new turns accumulate. Its retrigger ceiling stays inside the hard budget.

The two defaults work together. The margin protects the provider boundary. Hysteresis avoids changing the prompt on every turn and helps keep LLM cache hits.

A positive `summary_threshold` sets an earlier absolute trigger on estimated history. The resolved safety-bounded budget caps it. `trimmer.max_tokens`, `trimmer.reserve_tokens`, and `trimmer.safety_margin_ratio` override the matching assembly limits. A zero margin is possible. It is not recommended with an estimated counter.

```yaml
spec:
  models:
    - id: main
      model: any
      max_tokens: 12000
      # context_window omitted: resolve from the runtime model
  context_manager:
    type: summarising
    params:
      keep_turns: 10
      hysteresis_ratio: 0.1
      trimmer:
        reserve_tokens: 12000
        safety_margin_ratio: 0.1
```

If your provider has an authoritative window that differs from the catalog, set it in infra:

```yaml
# LLMProxy or LLMLocal infra
spec:
  generation:
    context_window: 200000
```

For a proxy or a negotiated rate, an infra owner can also override the catalog's per-million-token pricing:

```yaml
spec:
  generation:
    pricing:
      input_per_million_tokens: 0.30
      cached_input_per_million_tokens: 0.03
      output_per_million_tokens: 2.50
```

The runtime estimates the full request with a conservative `characters / 4` heuristic plus message and tool overhead. The estimate covers tool schemas and text messages. It is not a provider tokenizer, so multimodal payloads and model-specific tokenization can differ.

Provider-reported `prompt_tokens` is recorded after the call. Use it to calibrate.

The final trim keeps complete tool-call and result groups intact. If pinned context alone exceeds the budget, the runtime keeps it and emits a warning. It never silently deletes instructions.

## Inspecting Context Usage

Every assembled LLM call records a `context.assembled` event. It carries:

- the estimated request total and token breakdown,
- the context window, completion reserve, and remaining budget,
- metadata-only context parts (`source`, `section_id`, `placement`, `tokens`, `pinned`).

The latest snapshot is also available through `ControlContract` and the JSON-lines control protocol (see [Kernel operations](kernel-primitives.md)):

```text
inspect_context(session_id)
```

The returned `ContextUsageView` includes:

- The latest request's resolved model and window, estimated prompt tokens, reserve, remaining budget, fill ratio, and token breakdown.
- Metadata for pinned and non-pinned context parts, without their text.
- The most recent provider usage report, when available. `provider_usage_source` is `provider`, `cached_response`, or `unavailable`.
- Request-level cache hit and miss counts and rate, plus raw probe counts by cache layer. A request that passes through nested caches counts once in the overall rate and once per probed layer.
- The cumulative estimated provider cost for this session, and `cost_status`. Response-cache hits add zero provider cost.

Inspection is read-only. It does not reassemble the prompt, call the model, or change cache keys. The snapshot is the latest assembled request. It is unavailable before the first model call.

## Tokens, Cache, and Cost

MAS Lab records the provider usage fields it receives: `prompt_tokens`, `completion_tokens`, and provider-specific cached-token details. It records a response-cache hit separately, by cache layer (`infra_llm_cache` or `provider_cache`).

A strict infra cache miss is a `cache.lookup` event, not a fabricated model completion. The runtime computes the hit rate per cache layer from observed lookups. Write-only recording has no lookup denominator.

A cached response may carry the usage of the original live request. That usage helps context analysis. It is **not** new billable usage.

Provider prompt caching is different. The request still reaches the provider. Cached input tokens show up in provider usage if the provider supports it.

The model catalog can store versioned pricing rates. Infra `generation.pricing` overrides them. As of 2026-10-06, the catalog records Vertex AI Gemini 2.5 Flash text rates from the [Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing):

- $0.30/M uncached input.
- $0.03/M cached input.
- $2.50/M output.

These rates are an estimate. A LiteLLM proxy may use another region, account discount, or markup. Set the infra override to model that deployment. Models without known rates stay unpriced (`cost_status: pricing_or_usage_unavailable`).

For live provider calls, estimated cost is:

```text
uncached_input_tokens × input_rate
+ cached_input_tokens × cached_input_rate
+ output_tokens × output_rate
```

Response-cache hits cost zero upstream. Provider prompt-cache hits use the configured cached-input rate. Cost is cumulative over the recorded `LLM_CALL` returns of the session. It is not a billing invoice. Exact invoices may differ by region, tier, discounts, retries, multimodal tokens, and proxy billing policy.

## Cache Stability

The response cache key depends on the exact prompt, tool definitions, and model parameters. Context order and tool order therefore change behaviour. Do not add timestamps, random identifiers, or unstable ordering to prompt content.

The context snapshot is telemetry only. It does not enter the request or the cache key. Hysteresis, stable system and context parts, and unchanged tool schemas keep requests identical between compaction points.

## Framework Practice

MAS Lab assembles context locally. The demo runs through OpenAI-compatible Chat Completions, including Gemini via LiteLLM. The OpenAI Responses API server-side compaction item is not available on this route.

- [LangGraph short-term memory](https://docs.langchain.com/oss/python/langgraph/add-memory#manage-short-term-memory) documents token-aware trim and summarize steps and keeps tool-message groups valid.
- [Anthropic context windows](https://platform.claude.com/docs/en/docs/build-with-claude/context-windows) count system prompts, messages, tool results, tool definitions, and output. The provider also offers token counting, context editing, and compaction.
- [OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction) compacts at a configured threshold and returns a continuation item for the Responses API.

The common pattern has five parts:

1. Measure the whole request.
2. Reserve output space.
3. Compact before the hard provider limit.
4. Preserve recent and tool-boundary state.
5. Expose the exact budget decision to debugging tools.

## See also

- [Context assembly](../manifests/context-assembly.md)
- [Conversation summarization](../manifests/summarization.md)
- [LLM cache](llm-cache.md)
- [Kernel operations](kernel-primitives.md)
- [Tutorial 11: sessions and recovery](../tutorials/11-sessions-and-recovery/README.md)
