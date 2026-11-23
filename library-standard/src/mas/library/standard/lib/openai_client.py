#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An OpenAI SDK client for a judge, an embedding or a dataset step that speaks the OpenAI wire.

The key follows the rule of the LLM layers (:func:`mas.runtime.registry.llm_provider_registry.provider_key_ref`):
the provider's default secret goes only to its own default endpoint; any other host needs its own ``api_key``
SecretRef, and an optional one that is not set sends no auth header. A key goes only over https or to a
loopback host (:func:`mas.runtime.secrets.check_plain_http`), and a loopback host is reached without the
environment's proxy. The base URL is always passed: the SDK would otherwise read ``OPENAI_BASE_URL`` and
``OPENAI_API_KEY`` from the process environment.
"""

from __future__ import annotations

from typing import Any

from mas.runtime.secrets import check_plain_http, current_resolver
from mas.runtime.secrets.transport import httpx_client_kwargs_for

_PROTOCOL = "openai"


def _provider(cls: Any) -> Any:
    if cls is not None:
        return cls
    from mas.runtime.registry.llm_provider_registry import llm_provider_class

    return llm_provider_class(_PROTOCOL)


def openai_api_base(api_base: Any, *, cls: Any = None) -> str:
    """The configured base URL, else the provider's default endpoint."""
    base = str(api_base or "").strip()
    return base or str(getattr(_provider(cls), "default_api_base", "") or "")


def openai_key_ref(api_key: Any, api_base: Any, *, cls: Any = None, field: str = "api_key") -> Any:
    """The SecretRef sent to *api_base* (``SecretRefError`` when the default secret may not go there)."""
    from mas.runtime.registry.llm_provider_registry import provider_key_ref

    return provider_key_ref(api_key, api_base, _provider(cls), field=field)


def reveal_openai_key(api_key: Any, api_base: Any, *, cls: Any = None, field: str = "api_key") -> str:
    """The key for *api_base*, revealed now; ``""`` when an optional ref is not set (no auth header is sent)."""
    key = current_resolver().reveal_or_none(openai_key_ref(api_key, api_base, cls=cls, field=field)) or ""
    if key:
        check_plain_http(openai_api_base(api_base, cls=cls), what="API key")
    return key


def openai_client_for_key(
    api_key_ref: Any, api_base: Any, *, cls: Any = None, field: str = "api_key", **kwargs: Any
) -> Any:
    """An ``openai.OpenAI`` client for one run of the caller; the key is revealed now and kept by the client only."""
    key = reveal_openai_key(api_key_ref, api_base, cls=cls, field=field)
    return build_openai_client(key, api_base, cls=cls, **kwargs)


def build_openai_client(key: str, api_base: Any, *, cls: Any = None, **kwargs: Any) -> Any:
    """An ``openai.OpenAI`` client for a key from :func:`reveal_openai_key` (``""``: no auth header)."""
    try:
        import openai
    except ImportError as exc:
        raise ImportError("OpenAI client not available. Install with: pip install openai") from exc

    base = openai_api_base(api_base, cls=cls)
    if key:
        check_plain_http(base, what="API key")
    direct = httpx_client_kwargs_for(base)
    if direct and "http_client" not in kwargs:
        kwargs["http_client"] = openai.DefaultHttpxClient(**direct)
    if key:
        return openai.OpenAI(api_key=key, base_url=base, **kwargs)

    class _NoAuthOpenAI(openai.OpenAI):
        """Sends no Authorization header. The SDK refuses an empty key string and reads OPENAI_API_KEY
        for ``None``; a key provider returning ``""`` passes its constructor, and the header check that
        would refuse a request without the header is turned off for this client only."""

        def _validate_headers(self, headers: Any, custom_headers: Any) -> None:
            return None

    return _NoAuthOpenAI(api_key=_no_key, base_url=base, **kwargs)


def _no_key() -> str:
    return ""
