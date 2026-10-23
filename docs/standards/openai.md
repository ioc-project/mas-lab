<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# OpenAI Chat Completions

<p class="eyebrow">Open standards</p>

MAS-Lab calls language models over the
[OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat)
HTTP API: `POST {api_base}/chat/completions`. OpenAI, Ollama, LiteLLM, and
other OpenAI-compatible proxies all answer that request. The runtime does not
use the Assistants API or the Responses API.

## How MAS-Lab implements it

`OpenAILLMProvider` in `mas-library-standard` is the `LLMProvider` plugin.
It speaks the wire protocol with `httpx` (not the `openai` Python SDK). Infra
selects it:

```yaml
# bundled as standard:openai
apiVersion: infra/v1
kind: LLMProxy
metadata:
  name: openai
spec:
  provider: openai
  api_base: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
```

Pass `--infra-ref standard:openai` or set `OPENAI_API_KEY`. For a proxy, use
`standard:llm-proxy` and `LLM_PROXY_API_BASE`. Sampling, reasoning, streaming,
and extra body fields are
[`spec.models[]`](../manifests/agent.md) and the
[LLM reasoning](../manifests/llm-reasoning.md) manifest.

The lab bench package depends on `openai>=1.0.0` for evaluation helpers. That
is separate from the runtime provider.

## Dependencies

| Piece | Package |
| --- | --- |
| Chat Completions client | `mas-library-standard` (`OpenAILLMProvider`, `httpx`) |
| Bundled infra | `standard:openai`, `standard:llm-proxy`, `standard:llm-proxy-cached` |
| Optional eval SDK | `openai` on `mas-lab-bench` |

## Compliance

The provider implements the Chat Completions request and response shape that
OpenAI-compatible servers expect: messages, tools, streaming (`text/event-stream`),
and reasoning payloads where the server supports them. There is no separate
OpenAI TCK. Behaviour is covered by provider unit tests and live tests against
a compatible endpoint.

## Related

- [Tutorial 0 — Environment setup](../tutorials/00-environment-setup/README.md)
- [LLM cache](../manifests/llm-cache.md) and [LLM cache reference](../references/llm-cache.md)
- [LLM reasoning](../manifests/llm-reasoning.md)
- [LLM model catalog](../manifests/llm-model-catalog.md)
- [Contracts](../references/contracts.md) (`LLMProvider`)
