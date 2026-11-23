<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Open Responses

<p class="eyebrow">Open standards</p>

[Open Responses](https://www.openresponses.org/) is an open specification for
model calls built on typed items: `POST {api_base}/responses` takes a list of
input items and returns a list of output items (messages, function calls,
reasoning). Providers and gateways that implement it answer the same request.
Its field names (`prompt_cache_key`, `prompt_cache_retention`, `cached_tokens`,
`reasoning_tokens`) are the names MAS-Lab uses for the portable
[prompt cache](../manifests/llm-prompt-cache.md) and usage fields on every
layer.

## How MAS-Lab implements it

`OpenResponsesLLMProvider` in `library-standard` is the Open Responses layer over
`http`. `protocol: openresponses` plus a url is sugar for
`[http, openresponses]`. It posts with `httpx` and no vendor SDK:

```yaml
# bundled as standard:openresponses
apiVersion: infra/v1
kind: LLMProxy
metadata:
  name: openresponses
spec:
  protocol: openresponses
  proxy:
    api_base: https://api.openai.com/v1
    api_key: {secret: openresponses}
```

Pass `--infra-ref standard:openresponses` and set the key. The layer is
stateless: it sends `store: false` and the whole history on every call, so the
agent's working memory stays the single source of truth. A reasoning item's
`encrypted_content` is kept in `provider_state` and replayed with its tool
call. The field mapping is in [LLM providers](../manifests/llm-providers.md).

## Dependencies

| Piece | Package |
| --- | --- |
| Open Responses client | `library-standard` (`OpenResponsesLLMProvider`, `httpx`) |
| Bundled infra | `standard:openresponses` |

## Related

- [LLM providers](../manifests/llm-providers.md)
- [LLM prompt cache](../manifests/llm-prompt-cache.md)
- [LLM reasoning](../manifests/llm-reasoning.md)
- [Communication stack](../references/comm-stack.md)
