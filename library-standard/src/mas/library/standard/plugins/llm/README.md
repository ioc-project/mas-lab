<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# LLM provider plugins

Vendor HTTP and cache wrapping live here, not in `mas.runtime`. The runtime
owns `LLMProvider`, the registry, model catalog, and payload mapping.

| Plugin | Role | Module |
|--------|------|--------|
| `openai` | OpenAI-compatible `/chat/completions` (OpenAI, Azure, Ollama, LiteLLM, vLLM) | `plugins/llm/openai.py` |
| `cache` | Disk-cache decorator around a routed protocol plugin | `plugins/llm/cache.py` |

Register additional wire protocols the same way as tools: `type: llm_provider`
in `library.yaml`, then claim models with `spec.models[].kind`. Infra
`spec.protocol` (default `openai`) fills `kind` when the agent omits it.

Do **not** put `kind: cache` on `spec.models[]`. The only LLM response cache
is the `llm_cache` infra step (`--cache-read` / `--cache-write`,
`MAS_LLM_CACHE_READ` / `_WRITE`, or `RuntimeEngine` `cache.read` / `write`).
A future Bedrock plugin is a new class in this package, not a runtime
`model_access.module_path`.

Reasoning / sampling surface: [`docs/manifests/llm-reasoning.md`](../../../../../../../docs/manifests/llm-reasoning.md).
Catalog: [`docs/manifests/llm-model-catalog.md`](../../../../../../../docs/manifests/llm-model-catalog.md).
Overlay examples: `docs/schemas/examples/overlays/llm-reasoning.yaml`,
`llm-sampling.yaml`.
