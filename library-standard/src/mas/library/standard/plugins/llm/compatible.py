#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Named openai-compatible hosts. Same chat-completions wire as ``openai``."""

from __future__ import annotations

from typing import Any, ClassVar

from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.secrets.ref import SecretRef


class OpenAICompatibleLLMProvider(OpenAILLMProvider):
    """Chat-completions layer with this class's default host and key name."""

    remap_max_completion_tokens: ClassVar[bool] = False
    # Most named hosts reject body fields they do not know; only these two are known to take the cache fields.
    forwards_prompt_cache_fields: ClassVar[bool] = False

    def _build_chat_request(self, **kwargs: Any) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        url, payload, headers, query = super()._build_chat_request(**kwargs)
        if self.remap_max_completion_tokens:
            if "max_completion_tokens" in payload and "max_tokens" not in payload:
                payload["max_tokens"] = payload.pop("max_completion_tokens")
            else:
                payload.pop("max_completion_tokens", None)
        return url, payload, headers, query


class GroqLLMProvider(OpenAICompatibleLLMProvider):
    """Groq. ``protocol: groq`` plus a url is ``[http, groq]``."""

    provider_id = "groq"
    kind = "groq"
    default_api_base = "https://api.groq.com/openai/v1"
    default_secret = SecretRef(name="groq")
    remap_max_completion_tokens = True


class OpenRouterLLMProvider(OpenAICompatibleLLMProvider):
    """OpenRouter. ``protocol: openrouter`` plus a url is ``[http, openrouter]``."""

    provider_id = "openrouter"
    kind = "openrouter"
    default_api_base = "https://openrouter.ai/api/v1"
    default_secret = SecretRef(name="openrouter")
    forwards_prompt_cache_fields = True


class XaiLLMProvider(OpenAICompatibleLLMProvider):
    """xAI. ``protocol: xai`` plus a url is ``[http, xai]``."""

    provider_id = "xai"
    kind = "xai"
    default_api_base = "https://api.x.ai/v1"
    default_secret = SecretRef(name="xai")


class LiteLLMLLMProvider(OpenAICompatibleLLMProvider):
    """LiteLLM proxy. ``protocol: litellm`` plus a url is ``[http, litellm]``."""

    provider_id = "litellm"
    kind = "litellm"
    default_api_base = "http://localhost:4000/v1"
    default_secret = SecretRef(name="litellm")
    forwards_prompt_cache_fields = True


class CerebrasLLMProvider(OpenAICompatibleLLMProvider):
    """Cerebras. ``protocol: cerebras`` plus a url is ``[http, cerebras]``."""

    provider_id = "cerebras"
    kind = "cerebras"
    default_api_base = "https://api.cerebras.ai/v1"
    default_secret = SecretRef(name="cerebras")
    remap_max_completion_tokens = True


class MistralLLMProvider(OpenAICompatibleLLMProvider):
    """Mistral. ``protocol: mistral`` plus a url is ``[http, mistral]``."""

    provider_id = "mistral"
    kind = "mistral"
    default_api_base = "https://api.mistral.ai/v1"
    default_secret = SecretRef(name="mistral")
    remap_max_completion_tokens = True


class VllmLLMProvider(OpenAICompatibleLLMProvider):
    """vLLM. ``protocol: vllm`` plus a url is ``[http, vllm]``."""

    provider_id = "vllm"
    kind = "vllm"
    default_api_base = "http://localhost:8000/v1"
    default_secret = SecretRef(name="vllm")


class HuggingFaceLLMProvider(OpenAICompatibleLLMProvider):
    """Hugging Face router. ``protocol: huggingface`` plus a url is ``[http, huggingface]``."""

    provider_id = "huggingface"
    kind = "huggingface"
    default_api_base = "https://router.huggingface.co/v1"
    default_secret = SecretRef(name="hf")


class DatabricksLLMProvider(OpenAICompatibleLLMProvider):
    """Databricks model serving. ``protocol: databricks`` plus a url is ``[http, databricks]``."""

    provider_id = "databricks"
    kind = "databricks"
    default_api_base = ""
    default_secret = SecretRef(name="databricks")

    def _build_chat_request(self, **kwargs: Any) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        host = self.api_base.rstrip("/")
        if not host:
            raise RuntimeError("Databricks needs proxy.api_base (workspace host)")
        url, payload, headers, query = super()._build_chat_request(**kwargs)
        model = str(payload.get("model") or kwargs.get("model") or "").strip()
        url = f"{host}/serving-endpoints/{model}/invocations"
        return url, payload, headers, query
