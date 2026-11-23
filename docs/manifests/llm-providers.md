<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM providers

A **protocol layer** turns the portable LLM call (messages, tools, sampling, `reasoning`) into one provider's wire format and turns the reply back into an OpenAI-shaped message.
`protocol: <name>` plus a url on an infra `LLMProxy` selects the layer; it expands to `[http, <name>]`, so the layer posts through the shared http transport.
The spec is a portable **overset** (the union of what providers accept): each layer maps only what its wire supports, and the [model catalog](llm-model-catalog.md) decides per model what is allowed.
Infra fields (`api_base`, `api_key`, `timeout`) are in [infra.md](infra.md).

## Feature matrix

Each layer lists its `features` in `library-standard/library.yaml`.
A cell reads `yes` when the layer implements the feature today.
A test checks this table against `library.yaml`.

| Feature | Meaning |
|---------|---------|
| `tools` | Tool definitions and tool calls are mapped both ways |
| `streaming` | `stream: true` reads the provider's event stream and calls `on_stream_chunk` as text arrives |
| `structured_output` | `response_format` reaches the provider, so it constrains the answer |
| `prompt_cache` | The layer places provider cache markers ([llm-prompt-cache.md](llm-prompt-cache.md)) |
| `thinking` | The portable `reasoning` block is mapped to the provider's thinking controls |
| `vision` | Image inputs are mapped |

| Provider | tools | streaming | structured_output | prompt_cache | thinking | vision |
|----------|-------|-----------|-------------------|--------------|----------|--------|
| openai | yes | yes | yes | - | - | - |
| azure | yes | yes | yes | - | - | - |
| anthropic | yes | yes | yes | yes | yes | - |
| bedrock | yes | - | - | yes | yes | - |
| gemini | yes | yes | yes | - | yes | - |
| vertex | yes | yes | yes | - | yes | - |
| openresponses | yes | yes | yes | yes | yes | - |
| groq | yes | yes | yes | - | - | - |
| openrouter | yes | yes | yes | - | - | - |
| xai | yes | yes | yes | - | - | - |
| litellm | yes | yes | yes | - | - | - |
| cerebras | yes | yes | yes | - | - | - |
| mistral | yes | yes | yes | - | - | - |
| vllm | yes | yes | yes | - | - | - |
| huggingface | yes | yes | yes | - | - | - |
| databricks | yes | yes | yes | - | - | - |

`openai` and its inheritors forward `reasoning` fields as chat-completions fields (see [llm-reasoning.md](llm-reasoning.md)) but are not listed under `thinking`: the column is for layers that build a vendor thinking request from the catalog's `thinking_mode`.
Prompt caching on the openai family is automatic on the provider side; the layer only forwards `prompt_cache_key` and `prompt_cache_retention`.
Bedrock `stream: true` returns the whole text in one chunk, and Bedrock does not send `response_format` (the `structured` wrap layer still validates the answer).

## How each portable field maps

Every layer also accepts `extra` (merged into the body) and `extra_headers` / `extra_query`.

### openai, azure and the named hosts

`openai`, `azure`, `groq`, `openrouter`, `xai`, `litellm`, `cerebras`, `mistral`, `vllm`, `huggingface` and `databricks` share the chat-completions wire.

| Field | Wire |
|-------|------|
| system | A `system` message, unchanged |
| tools, `tool_choice` | `tools` and `tool_choice` as given |
| `response_format` | `response_format` as given |
| sampling | Every sampling field as a body field, bounded by the catalog; `temperature` is always sent |
| reasoning | `reasoning_effort` and the other fields of [llm-reasoning.md](llm-reasoning.md), catalog-gated |
| prompt cache | `prompt_cache_key` and `prompt_cache_retention` for `openai`, `azure`, `openrouter` and `litellm`; dropped on the other named hosts; `prompt_cache_breakpoints` never |
| usage | `prompt_tokens_details.cached_tokens` becomes `cached_tokens`; `completion_tokens_details.reasoning_tokens` becomes `reasoning_tokens` (also when streaming) |

Differences: Azure posts to `/openai/deployments/{name}/chat/completions` with `api-key` (or bearer with `proxy.auth: bearer`); Groq, Cerebras and Mistral send `max_tokens` instead of `max_completion_tokens`; Databricks posts to `/serving-endpoints/{name}/invocations`.

### anthropic

| Field | Wire |
|-------|------|
| system | `system`, system messages joined with a newline |
| tools, `tool_choice` | `tools` (`input_schema`); `required` becomes `{type: any}`, a named tool `{type: tool}`, `none` `{type: none}` |
| `response_format` | `output_config.format` of type `json_schema` |
| sampling | `temperature`, `top_p`, `top_k` only when the spec sets them and the catalog does not mark them unsupported; `stop` becomes `stop_sequences`; `max_tokens` defaults to 8192 |
| reasoning | Built from the catalog `thinking_mode` ([llm-reasoning.md](llm-reasoning.md#thinking-on-anthropic-bedrock-and-gemini)): `adaptive` sends `thinking: {type: adaptive}` and `output_config.effort`; `budget` sends `thinking: {type: enabled, budget_tokens}` kept at least 1024 below `max_tokens`; `always_on` sends only the effort. Effort `none` or `disable` sends nothing. While thinking is on, `temperature`, `top_p`, `top_k` and a forced `tool_choice` are not sent. `exclude: false` asks for `display: summarized` and returns the text as `reasoning`. Signed thinking blocks of a tool turn come back in `provider_state` and are sent first on the next request; when a tool turn of the current loop has none (a resumed session), thinking is left off for that request |
| prompt cache | `cache_control` markers from `prompt_cache_breakpoints`, TTL from `prompt_cache_retention` (`24h` is sent as `1h`, the maximum) |
| usage | `input_tokens` plus cache tokens becomes `prompt_tokens`; `cache_read_input_tokens` becomes `cached_tokens`; `cache_creation_input_tokens` becomes `cache_write_tokens` (also when streaming) |

A refusal, or a tool call cut off by `max_tokens`, is a failed call. A failed tool result (`is_error`) is sent with `is_error: true`.

### bedrock

| Field | Wire |
|-------|------|
| system | `system` as a list of `{text}` blocks |
| tools, `tool_choice` | `toolConfig.tools` (`toolSpec`); `required` becomes `{any: {}}`, a named tool `{tool: {name}}`; `none` omits the tools |
| `response_format` | not sent |
| sampling | `inferenceConfig.temperature` only when the spec sets it and the catalog does not mark it unsupported; `top_p` becomes `topP`; `stop` becomes `stopSequences`; `max_tokens` becomes `maxTokens` |
| reasoning | For a model the catalog lists with a `thinking_mode`: `additionalModelRequestFields.thinking` and `output_config.effort`, as for Anthropic. `reasoningContent` blocks of a tool turn are kept in `provider_state` and replayed first. A model the catalog does not list gets nothing; use `extra.additionalModelRequestFields` |
| prompt cache | `cachePoint` blocks after the system content, the last tool and the last user turns; `ttl` `5m` or `1h` from `prompt_cache_retention`. Without a setting, markers go only on a model the catalog lists |
| usage | `inputTokens` plus cache tokens becomes `prompt_tokens`; `cacheReadInputTokens` becomes `cached_tokens`; `cacheWriteInputTokens` becomes `cache_write_tokens` |

Authentication is a bearer token (`AWS_BEARER_TOKEN_BEDROCK`). A failed tool result is sent with `status: error`.

### gemini and vertex

| Field | Wire |
|-------|------|
| system | `systemInstruction.parts` |
| tools, `tool_choice` | `tools[].functionDeclarations`; `toolConfig.functionCallingConfig` mode `NONE`, `ANY` (with `allowedFunctionNames` for a named tool) |
| `response_format` | `generationConfig.responseMimeType` and `responseJsonSchema` |
| sampling | `generationConfig`: `temperature`, `topP`, `topK`, `stopSequences`, `maxOutputTokens` |
| reasoning | `generationConfig.thinkingConfig` from the catalog `thinking_mode`: `budget` sends `thinkingBudget` (from `budget_tokens`, else the effort, else the catalog default, within the catalog bounds; effort `none` sends `0` only where the catalog allows it), `level` sends `thinkingLevel` `low` or `high`. `exclude: false` adds `includeThoughts` and the thought text comes back as `reasoning`, never in `content`. The `thoughtSignature` of each function call is kept in `provider_state` and replayed; a call without one gets the skip-validation signature |
| prompt cache | a `prompt_cache_key` that starts with `cachedContents/` is sent as top-level `cachedContent`; other values and the other two fields are ignored (implicit caching is automatic) |
| usage | `cachedContentTokenCount` becomes `cached_tokens`; `thoughtsTokenCount` becomes `reasoning_tokens` |

Vertex uses the project path (`proxy.project`, `proxy.location`) and a bearer token; the body is the same.
An error body on HTTP 200 is a failed call. A failed tool result is sent as `{error: ...}`.

### openresponses

| Field | Wire |
|-------|------|
| system | `instructions` |
| history | Typed `input` items: `message`, `function_call`, `function_call_output`, `reasoning`. The call is stateless (`store: false`) |
| tools, `tool_choice` | Flat `tools[]` (`type: function`, `name`, `parameters`); `tool_choice` `none`, `required`, or `{type: function, name}` |
| `response_format` | `text.format` `json_schema` |
| sampling | `temperature` and `top_p`, bounded by the catalog; `max_output_tokens` |
| reasoning | `reasoning.effort`, `reasoning.mode` and `include` from [llm-reasoning.md](llm-reasoning.md); `exclude: false` adds `reasoning.summary: auto` and the summary comes back as `reasoning`. With thinking on, `include` gets `reasoning.encrypted_content`, and each reasoning item is kept in `provider_state` and replayed before its tool call |
| prompt cache | `prompt_cache_key` and `prompt_cache_retention` (`in_memory` or `24h`); `prompt_cache_breakpoints` is never sent |
| usage | `input_tokens` / `output_tokens` become `prompt_tokens` / `completion_tokens`; `input_tokens_details.cached_tokens` becomes `cached_tokens`; `output_tokens_details.reasoning_tokens` becomes `reasoning_tokens` |

A `failed` response, an in-stream `error` event, a refusal with no other output, and a tool call cut off by `max_output_tokens` are failed calls.

## What is not supported

- Image and other non-text inputs on every layer (`vision`).
- Thinking controls for a model the catalog does not list on Bedrock (set `extra.additionalModelRequestFields`).
- `reasoning.mode`, `think` and `include` on Anthropic, Bedrock and Gemini (they are OpenAI-family and Ollama fields).
- A true event stream on Bedrock (`converse-stream`) and SigV4 request signing.
- `prompt_cache_breakpoints` on any wire without explicit markers (openai family, Gemini, Vertex).
- `top_k` on Bedrock and on the openai family's chat-completions wire (it passes only as an extra field).
- Sampling fields with no wire equivalent on a layer (for example `seed` or `logprobs` on Anthropic) are not sent.

## See also

- [LLM prompt cache](llm-prompt-cache.md)
- [LLM model catalog](llm-model-catalog.md)
- [LLM reasoning](llm-reasoning.md)
- [Infra manifests](infra.md)
