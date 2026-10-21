<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM model catalog

The **LLM model catalog** (`kind: LlmModelCatalog`) records per-model facts: **context window** (the maximum tokens a model reads in one request), **max output**, **defaults**, **allowed settings**, and optional **pricing**.
Read this page to learn which settings the runtime sends for a known model, or to edit the catalog when you contribute a model.

The runtime ships a curated YAML as package data.
No manifest field or CLI flag loads a different catalog file.
To override limits for one deployment, set infra `spec.models.generation` (`context_window`, `max_output_tokens`, `pricing`).
See [infra.md: LLM output-token policy](infra.md#llm-output-token-policy).

```yaml
apiVersion: mas/v1
kind: LlmModelCatalog
metadata:
  name: standard
  version: "2026.9.2"
spec:
  models:
    gpt-5:
      aliases: [openai/gpt-5]
      context_window: 400000
      max_output_tokens: 128000
```

Schema: [`llm-model-catalog.schema.yaml`](../schemas/runtime/llm-model-catalog.schema.yaml).
Data: `runtime/src/mas/runtime/spec/llm-model-catalog.yaml` (`metadata.version`).
Loader: `mas.runtime.engine.llm_model_catalog`.

## Why a curated catalog

The closest reusable community table is [LiteLLM `model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) (MIT).
It is large, changes daily, and is not a MAS schema, so the runtime does not import it wholesale.
The catalog lists it under `spec.sources` with `reusable: true`.

Other pointers (not in-process libraries):

| Source | URL | Reuse |
|--------|-----|--------|
| OpenRouter models API | https://openrouter.ai/api/v1/models | live HTTP (`supported_parameters`) |
| models.dev | https://models.dev | aggregated community catalog |
| Hugging Face model cards | https://huggingface.co/models | cards / TGI |
| vLLM OpenAI server | https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html | `think` / `chat_template_kwargs` |
| Ollama API | https://github.com/ollama/ollama/blob/main/docs/api.md | `think` |
| Aider `MODEL_SETTINGS` | https://github.com/Aider-AI/aider/blob/main/aider/resources/model-settings.yml | Aider-specific YAML |
| Continue.dev | https://github.com/continuedev/continue | product configs |

## How the catalog is applied

- **Lookup.** `ModelCatalog.get(name)` matches the model key, then an alias, then an alias equal to the last `/` segment of `name`. Do not name a model or alias `default`: it collides with that last-segment lookup and with the infra `models.mappings.default` convention.
- **Defaults.** When the spec omits a setting, the runtime uses the catalog `defaults` value. Examples: `reasoning.effort`, `reasoning.budget_tokens`, `think`, `reasoning.mode`, `temperature`.
- **Clamping.** The runtime clamps numeric spec values to a setting's `min` and `max`. `enum` documents the accepted values but is not enforced.
- **Gating.** On a known model, these reasoning knobs are **deny-by-default**: `reasoning.effort`, `reasoning.mode`, `reasoning.exclude`, `reasoning.budget_tokens`, `think`, and `include`. The runtime does not send a knob that the entry does not list. Other settings are allowed unless marked `supported: false`.
- **Limits.** `max_output_tokens` is an output ceiling. The lower of it and infra `generation.max_output_tokens` applies. `context_window` sizes the history when neither the agent nor infra sets one. Unknown models fall back to `128000` with a warning.
- **Unknown models.** The runtime sends the spec as written, and the catalog filters nothing.

## Full shape

```yaml
apiVersion: mas/v1
kind: LlmModelCatalog
metadata:
  name: standard
  version: "2026.9.2"
spec:
  sources:
    - id: litellm
      url: https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json
      reusable: true
  models:
    gpt-5:
      aliases: [openai/gpt-5]
      context_window: 400000
      max_output_tokens: 128000
      apis: [chat_completions, responses]
      defaults:
        reasoning.effort: medium
      settings:
        reasoning.effort: {enum: [none, minimal, low, medium, high, xhigh]}
        reasoning.mode: {enum: [standard, pro]}
        think: {supported: false}
```

| Model field | Meaning |
|-------------|---------|
| `aliases` | Other names that resolve to this entry (`openai/gpt-5`) |
| `context_window` | Input window in tokens |
| `max_output_tokens` | Output-token ceiling |
| `max_thinking_tokens` | Largest thinking budget the model accepts (informational) |
| `apis` | Wire APIs the model is served on: `chat_completions`, `responses`, `ollama`, `vllm`, `anthropic`, `gemini` |
| `defaults` | Values used when the spec omits a setting. `think.chat_template_kwargs: true` sends `think` as `chat_template_kwargs.enable_thinking` (vLLM chat templates) |
| `settings.<name>` | `supported` (default `true`), `min`, `max`, `default`, `enum` |
| `pricing` | USD per million tokens (`input_per_million_tokens`, `cached_input_per_million_tokens`, `output_per_million_tokens`) used for cost estimates |

`sources[]` entries take `id` (required), `title`, `url`, `note`, and `reusable` (default `false`).
Bump `metadata.version` when curated numbers change.

## See also

- [LLM reasoning / thinking](llm-reasoning.md)
- [agent.md: Output-token limits](agent.md#output-token-limits)
- [Summarization](summarization.md): context-window sizing
- [Infra manifests](infra.md): per-deployment overrides
