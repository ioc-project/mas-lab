#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Vertex generateContent as a Gemini subclass over http.

Same body as gemini. The URL is the Vertex project path. Bearer token.
``protocol: vertex`` plus a url is ``[http, vertex]``.
"""

from __future__ import annotations

from urllib.parse import quote

from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider, _model_id
from mas.runtime.secrets.ref import SecretRef


class VertexLLMProvider(GeminiLLMProvider):
    """Vertex generateContent layer. ``protocol: vertex`` plus a url is ``[http, vertex]``."""

    provider_id = "vertex"
    kind = "vertex"
    default_api_base = "https://us-central1-aiplatform.googleapis.com"
    default_secret = SecretRef(name="vertex")

    def _request_url(self, model: str, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        proxy = self.protocol_params()
        project = str(proxy.get("project") or "").strip()
        if not project:
            raise RuntimeError("Vertex needs proxy.project (Google Cloud project id)")
        location = str(proxy.get("location") or "us-central1").strip()
        host = self.api_base.rstrip("/")
        if host == self.default_api_base:
            # Vertex serves each region from its own host; only the default host is derived from the location.
            host = (
                "https://aiplatform.googleapis.com"
                if location == "global"
                else f"https://{location}-aiplatform.googleapis.com"
            )
        project = quote(project, safe="")
        location = quote(location, safe="")
        return (
            f"{host}/v1beta1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{_model_id(model)}:{action}"
        )

    def _auth_header(self) -> tuple[str, str]:
        return "Authorization", "Bearer "
