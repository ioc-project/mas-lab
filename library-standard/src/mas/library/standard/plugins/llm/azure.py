#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Azure OpenAI as a protocol layer over an http transport.

Classic deployments: ``POST {api_base}/openai/deployments/{name}/chat/completions``.
A host that already ends in ``/v1`` keeps ``/chat/completions``. Header is
``api-key``. ``proxy.auth: bearer`` sends ``Authorization`` instead.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.secrets.ref import SecretRef

_DEFAULT_API_VERSION = "2024-10-21"


class AzureLLMProvider(OpenAILLMProvider):
    """Azure OpenAI layer. ``protocol: azure`` plus a url is ``[http, azure]``."""

    provider_id = "azure"
    kind = "azure"
    default_api_base = ""
    default_secret = SecretRef(name="azure")

    def _auth_header(self) -> tuple[str, str]:
        if str(self.protocol_params().get("auth") or "api-key").strip().lower() in {"bearer", "token"}:
            return "Authorization", "Bearer "
        return "api-key", ""

    def _build_chat_request(self, **kwargs: Any) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        url, payload, headers, query = super()._build_chat_request(**kwargs)
        proxy = self.protocol_params()
        if _is_v1(self.api_base):
            return url, payload, headers, query
        deployment = str(proxy.get("deployment") or kwargs.get("model") or "").strip()
        version = str(proxy.get("api_version") or _DEFAULT_API_VERSION).strip()
        host = self.api_base.rstrip("/")
        if not host.endswith("/openai"):
            host = f"{host}/openai"
        url = f"{host}/deployments/{deployment}/chat/completions"
        query = dict(query or {})
        query["api-version"] = version
        return url, payload, headers, query


def _is_v1(api_base: str) -> bool:
    segments = [part for part in urlparse(api_base).path.split("/") if part]
    return "v1" in segments
