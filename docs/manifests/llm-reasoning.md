<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM reasoning / thinking

The `reasoning` block of a model entry (`spec.models[].reasoning` on [`kind: Agent`](agent.md)) controls how a reasoning model thinks before it answers.
Reasoning models include OpenAI o-series and GPT-5, Gemini 2.5, DeepSeek-R1, gpt-oss, and Qwen3.
They spend **hidden thinking tokens** (chain-of-thought, CoT) before the visible answer.
The runtime keeps those tokens out of working memory, so they never look like the assistant reply.

Set `reasoning` to change the effort, cap the thinking budget, or choose whether the model's thoughts appear in the message.
The thinking budget (reasoning budget) is the maximum number of thinking tokens a model may spend.

```yaml
spec:
  models:
    - model: gpt-5
      reasoning:
        effort: low
```

Schema: `agent.schema.yaml`.
Protocol plugin: OpenAI-compatible Chat Completions (`OpenAILLMProvider`).

## Contents

- [Spec vs contract vs catalog](#spec-vs-contract-vs-catalog)
- [What the APIs expose](#what-the-apis-actually-expose)
- [Spec surface](#spec-surface-specmodels)
- [Why cache replay is unrelated](#why-cache-replay-is-unrelated)
- [Examples](#example)

## Spec vs contract vs catalog

An *overset* is the union of the fields that different vendors accept.
Each backend receives only the subset it supports.

- **Spec** (`spec.models[]`) is the static overset: sampling, `reasoning`, and `extra`, a passthrough for vendor fields the spec does not name.
- **Contract** (`LLMProvider.chat_completion`) is the per-call overset: `stream`, `stream_options`, `tool_choice`, `extra_body`, `extra_headers`, `extra_query`.
- **Catalog** ([llm-model-catalog.md](llm-model-catalog.md)) holds per-model `default`, `min`, `max`, `enum`, and `supported`, like context-window tables.

Unknown models are not deny-by-default.
For known models, the runtime omits fields that the backend does not advertise.
This keeps Azure from returning HTTP 400 on a nested `reasoning` object.

## What the APIs actually expose

These are the knobs that vendors document.
Named spec fields are the portable overset.
Anything else goes in `spec.models[].extra` or `extra_body`.

| Knob | OpenAI Chat Completions | OpenAI Responses | LiteLLM / OpenRouter | Ollama | Anthropic | Gemini |
|------|-------------------------|------------------|----------------------|--------|-----------|--------|
| Effort / depth | `reasoning_effort`: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `reasoning.effort` plus `reasoning.mode` (`standard` / `pro`, GPT-5.6) | `reasoning.effort` (alias `reasoning_effort`) | `think`: `true`/`false` or `low`/`medium`/`high` | adaptive thinking; effort mapped by LiteLLM | `thinkingConfig.thinkingBudget` |
| Thinking-token budget | billed inside `max_completion_tokens` (not a separate field) | `max_output_tokens` | `reasoning.max_tokens` | not a token budget | `thinking.budget_tokens` | `thinkingBudget` (0 = off) |
| Hide CoT from the message | hosted models already omit raw CoT | opaque `reasoning` items; optional `encrypted_content` | `reasoning.exclude: true` (OpenRouter convention) | `message.thinking` is separate; Chat Completions `exclude` is incomplete | thinking blocks | `includeThoughts` |
| Answer token cap | `max_completion_tokens` on reasoning models (`max_tokens` is rejected) | `max_output_tokens` | either | `num_predict` | `max_tokens` includes thinking | `maxOutputTokens` |

### Vendor-specific knobs on the spec

Three knobs are Responses-only or unofficial on Chat Completions.
Each is a named spec field and is catalog-gated.

| Field | Caveat | Behaviour |
|-------|------------------------|-----|
| `reasoning.mode` (`standard` / `pro`) | GPT-5.6 Responses; may return 400 on `api.openai.com` Chat Completions | Sent as `reasoning.mode`. |
| `reasoning.include` (e.g. `reasoning.encrypted_content`) | Responses multi-turn encrypted CoT | Sent as top-level `include`. |
| `reasoning.think` | Ollama / vLLM / gpt-oss; LiteLLM's OpenAI wrapper rejects top-level `think` | Ollama keeps `think`. Native vLLM flattens it to `chat_template_kwargs.enable_thinking` in the JSON body. |

[llm-model-catalog.md](llm-model-catalog.md) lists per-model defaults, maxima, and support.
`llm-sampling.schema.yaml` defines the sampling overset (temperature, top_p, min_p, and others).

## Spec surface (`spec.models[]`)

```yaml
models:
  - id: main
    model: gpt-5
    kind: openai
    temperature: 0.7
    max_tokens: 2000          # answer budget (see mapping below)
    reasoning:
      effort: low             # none | disable | minimal | low | medium | high | xhigh | max
      budget_tokens: 1024     # optional thinking-token cap
      exclude: true           # default; omit CoT from the assistant message
      mode: standard          # optional; Responses / LiteLLM
      think: true             # optional; Ollama / vLLM
      include:                # optional; Responses-style extras
        - reasoning.encrypted_content
```

`reasoning_effort: low` remains a deprecated alias for `reasoning.effort`.
If you set both, the nested object wins.

| Field | Default | Effect |
|-------|---------|--------|
| `reasoning.effort` | omitted | Sent as Chat Completions `reasoning_effort`. `none` / `disable` turn thinking off where the backend allows it (GPT-5.1+, Ollama via LiteLLM). gpt-oss cannot fully disable thinking: use a low effort and `exclude`. |
| `reasoning.budget_tokens` | omitted | Sent as `reasoning.max_tokens` (OpenRouter / Anthropic style). Catalog-gated. Independent of `max_tokens`. `0` means "no thinking budget" on Gemini via LiteLLM. |
| `reasoning.exclude` | **true** | MAS strips `reasoning`, `reasoning_content`, and `thinking` fields, and `<think>` / `<thinking>` blocks, from the assistant message and from streamed `content` deltas. Local stripping always applies. When the spec sets `exclude` explicitly and the catalog allows it, the plugin also sends `reasoning.exclude` for proxies that follow the OpenRouter convention. Official OpenAI Chat Completions may reject that extension, so the plugin does not send it otherwise. |
| `reasoning.mode` | omitted | Sent as `reasoning.mode` (`standard` / `pro`). Catalog-gated. |
| `reasoning.think` | omitted | Sent as top-level `think` (Ollama). When the catalog entry sets `defaults.think.chat_template_kwargs: true` (vLLM-served gpt-oss, Gemma), it is sent as `extra_body.chat_template_kwargs.enable_thinking` instead, and string values also set `thinking_level`. Catalog-gated. LiteLLM's OpenAI-compatible endpoint often rejects both forms. |
| `reasoning.include` | omitted | Sent as top-level `include` (e.g. `reasoning.encrypted_content`). Catalog-gated. |

On a model listed in the [catalog](llm-model-catalog.md), omitted `effort`, `budget_tokens`, `think`, and `mode` take the catalog `defaults`.
The runtime clamps `budget_tokens` to the catalog `min` and `max`.
It drops fields that the catalog does not mark as supported from the request.

### Token fields

When `effort` is set, is not `none` or `disable`, and is sent, the OpenAI plugin sends `max_completion_tokens` (the o-series and GPT-5 Chat Completions field).
It omits `max_tokens`, which those models reject.
Models whose catalog entry sets `reasoning.max_completion_tokens: {supported: false}` keep `max_tokens`.
The shipped Gemini, Llama, gpt-oss, and Gemma entries do this.

The value is still `spec.models[].max_tokens`, the **answer** budget.
OpenAI bills hidden reasoning inside it.
`budget_tokens` is the extra cap for backends that expose a separate thinking budget.
An explicit `max_completion_tokens` wins over this mapping.
The request never carries both fields, and carries neither when no limit is configured.
Defaults, ceilings, and `on_truncation` are in [agent.md: Output-token limits](agent.md#output-token-limits).

`reasoning_effort` is a top-level Chat Completions field.
The nested `reasoning` object carries only `budget_tokens`, `exclude`, and `mode`.
Strict OpenAI and Azure Chat Completions return HTTP 400 on an unexpected nested `reasoning` object, so effort is not duplicated inside it.

## Why cache replay is unrelated

Offline CI replays a stored assistant message after sanitization.
Configure reasoning on the **live** protocol plugin, then record.

## Example

Keep thinking cheap and never show CoT:

```yaml
spec:
  models:
    - model: gpt-5-mini
      kind: openai
      max_tokens: 1500
      reasoning:
        effort: low
        exclude: true
```

Cap Gemini-style thinking tokens through an OpenAI-compatible proxy:

```yaml
spec:
  models:
    - model: gemini-2.5-pro
      kind: openai
      max_tokens: 2048
      reasoning:
        budget_tokens: 512
        exclude: true
```

### Overlays

Agent overlays can patch `spec.models[]`.
The patch deep-merges by `id`, then `model`, then `main`.
Connection fields (`api_base`, `api_key_env`) stay infra-only.

```yaml
kind: Overlay
spec:
  target:
    kind: Agent
  patch:
    models:
      - id: main
        reasoning:
          effort: low
          budget_tokens: 512
          exclude: true
          think: true
```

The runtime still reads the deprecated `spec.llm.reasoning` (and overlay `patch.llm.reasoning`).
It does so only when the matching `models[]` entry has no `reasoning` block.

## See also

- [llm-model-catalog.md](llm-model-catalog.md): per-model defaults, maxima, and support
- [Agent manifest](agent.md#modelentry): the full `ModelEntry`
- [Summarization](summarization.md): token thresholds and the summarizer model
