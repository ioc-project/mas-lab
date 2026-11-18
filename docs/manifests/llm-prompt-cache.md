<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM prompt cache

A **prompt cache** is a provider feature: the provider keeps the start of a prompt (the system prompt, the tool list, the older turns) after one call and reuses it on the next call that begins with the same text.
A **cache read** is billed at a fraction of the normal input price and is faster.
A **cache write** is the first call that stores the prefix; some providers bill it above the normal input price.
The saving shows up in the `usage` of each reply.

This page covers the provider's cache.
It is not the [`llm_cache`](llm-cache.md) infra middleware, which records whole responses to a file and replays them without calling the provider at all.
The two stack: a replayed call never reaches the provider, so it never touches the prompt cache.

## Contract

Three optional fields on a `spec.models[]` row (and on the infra `LLMProxy` defaults) describe the cache.
They are part of the portable [sampling overset](llm-reasoning.md#spec-vs-contract-vs-catalog): each protocol sends only what its wire supports, and the [model catalog](llm-model-catalog.md) decides per model what is allowed.
Schema: [`llm-sampling.schema.yaml`](../schemas/runtime/fragments/llm-sampling.schema.yaml).
The names follow [Open Responses](https://github.com/openresponses/openresponses).

| Field | Values | Meaning |
|-------|--------|---------|
| `prompt_cache_key` | string | Groups requests that share a prefix so the host routes them to the same cache. On Gemini and Vertex, a `cachedContents/...` name of a cache you created. |
| `prompt_cache_retention` | `in_memory`, `5m`, `24h`, `1h` | How long the cached prefix is kept. `in_memory` and `5m` are the short default; `24h` and `1h` ask for the long kind. |
| `prompt_cache_breakpoints` | `false`, or an object `{system, tools, messages}` | Where an explicit-cache protocol marks the prompt. Absent: system prompt, last tool, last two user turns. `false`: no marker. Object: `system` and `tools` are booleans, `messages` is 0 to 2 user turns; an omitted part is off. |

```yaml
spec:
  models:
    - model: claude-opus-5
      prompt_cache_retention: 1h
      prompt_cache_breakpoints: {system: true, tools: true, messages: 1}
    - model: gpt-5
      prompt_cache_key: triage-v3
      prompt_cache_retention: 24h
```

A **breakpoint** is a marker the provider reads as "cache everything up to here".
Anthropic allows four markers per request; the defaults use at most four (system, tools, two user turns).

## How each protocol maps the fields

| Protocol | `prompt_cache_key` | `prompt_cache_retention` | `prompt_cache_breakpoints` |
|----------|--------------------|--------------------------|----------------------------|
| `anthropic` | not sent | `in_memory` / `5m`: `cache_control: {type: ephemeral}`; `24h` / `1h`: adds `ttl: "1h"` (`24h` maps to the 1h maximum) | `cache_control` on the system block, the last tool, the last N user turns. Default on. |
| `bedrock` | not sent | `cachePoint` gets `ttl` `5m` or `1h` when set; none when unset | `{"cachePoint": {"type": "default"}}` after the system content, after the last tool spec, after the last N user turns. Default on only for a model the catalog lists; an explicit setting always applies. |
| `gemini`, `vertex` | a `cachedContents/...` name is sent as top-level `cachedContent`; any other value is ignored (implicit caching is automatic) | ignored | ignored |
| `openai`, `azure` | sent as `prompt_cache_key` | sent as `prompt_cache_retention` | never sent (the wire has no field) |
| `openresponses` | sent as `prompt_cache_key` | sent as `prompt_cache_retention` (`in_memory` or `24h`) | never sent |
| `openrouter`, `litellm` | sent | sent | never sent |
| `groq`, `xai`, `cerebras`, `mistral`, `vllm`, `huggingface`, `databricks` | dropped (the host may reject unknown fields) | dropped | never sent |

A host sends the cache fields when its class sets `forwards_prompt_cache_fields = True`.

## Catalog gating

The catalog lists a setting per model as `supported: false` to keep it off the wire.
`prompt_cache_breakpoints: {supported: false}` on the `gpt-*` entries stops markers; `prompt_cache_retention` and `prompt_cache_key` stay supported.
Gemini entries support the key (as a cached-content name) and not the other two.
Claude entries support all three.
A model the catalog does not list gets the explicit spec as written, and the protocol default for markers where it has one.

The same catalog also decides sampling for Anthropic and Bedrock: they send `temperature`, `top_p` and `top_k` only when the spec sets them and the catalog does not mark them unsupported for that model.
Current Claude models list all three as unsupported.

## Cost and reading `usage`

Every reply carries `usage` with these normalized fields, whatever the protocol:

| Field | Meaning |
|-------|---------|
| `prompt_tokens` | All input tokens, cached ones included |
| `cached_tokens` | Input tokens read from a cache (cache read). Absent when the provider reports none. |
| `cache_write_tokens` | Input tokens written to a cache (cache write). Anthropic and Bedrock only. |
| `reasoning_tokens` | Thinking tokens, where the provider reports them |

On Anthropic and Bedrock the raw `input_tokens` excludes cached tokens; the layer adds them back into `prompt_tokens`, so `cached_tokens` is always a part of `prompt_tokens`.
To see the saving, compare `cached_tokens / prompt_tokens` across calls: the first call writes (`cache_write_tokens` large, `cached_tokens` 0), the later calls read.
Anthropic bills a write above the input price and a read well below it, so a prefix pays off after one or two reuses.
Prices per model are in the catalog `pricing` block.

## See also

- [LLM cache](llm-cache.md): record and replay of whole responses
- [LLM providers](llm-providers.md): every protocol and the fields it maps
- [LLM model catalog](llm-model-catalog.md): per-model support markers
- [Infra manifests](infra.md): `LLMProxy` and `spec.protocol`
