#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HTTP protocol LLM layer: a wire plugin sits on an http transport."""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable

import httpx
from mas.library.standard.lib.stack.call import LayerLifecycle
from mas.library.standard.lib.tls import resolve_ssl_verify
from mas.library.standard.plugins.llm.http import arequest_once
from mas.library.standard.plugins.llm.structured import apply_structured_result, schema_from_format
from mas.runtime.engine.llm_reasoning import ReasoningSettings, coerce_reasoning_settings
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import (
    classify_llm_failure,
    classify_llm_http_error,
    safe_exception_text,
    shown_urls,
)
from mas.runtime.secrets.errors import SecretMissing, SecretRefError
from mas.runtime.secrets.ref import SecretRef, parse_secret_ref
from mas.runtime.secrets.resolver import SecretResolver, current_resolver
from mas.runtime.secrets.transport import PlainHttpRefused, httpx_client_kwargs_for, reveal_headers_for

logger = logging.getLogger(__name__)


def protocol_http_steps(
    layer: str,
    spec: dict[str, Any] | None,
    *,
    default_timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """``protocol: <layer>`` plus a url is sugar for ``[http, <layer>]``, bottom first."""
    raw = dict(spec or {})
    proxy = raw.get("proxy") if isinstance(raw.get("proxy"), dict) else raw
    api_base = str(proxy.get("api_base") or raw.get("api_base") or raw.get("url") or "").strip()
    http_params: dict[str, Any] = {}
    if api_base:
        http_params["url"] = api_base
    timeout = proxy.get("timeout")
    if timeout is None:
        timeout = raw.get("timeout")
    http_params["timeout"] = float(timeout) if timeout is not None else default_timeout
    for key in ("verify_ssl", "ca_bundle"):
        value = proxy.get(key) if proxy.get(key) is not None else raw.get(key)
        if value is not None:
            http_params[key] = value
    skip = {"inner", "pipeline", "layer", "middleware", "for", "per", "proxy", "api_key"}
    protocol_params = {k: v for k, v in raw.items() if k not in skip}
    return [
        {"layer": "http", "for": ["llm"], "params": http_params},
        {"layer": layer, "for": ["llm"], "params": protocol_params},
    ]


# Normalized usage field -> (flat wire keys, (detail object, key) wire paths), in order of preference.
_USAGE_FIELDS: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "cached_tokens": (
        ("cached_tokens", "cache_read_input_tokens", "cacheReadInputTokens", "cachedContentTokenCount"),
        (("prompt_tokens_details", "cached_tokens"), ("input_tokens_details", "cached_tokens")),
    ),
    "cache_write_tokens": (
        ("cache_write_tokens", "cache_creation_input_tokens", "cacheWriteInputTokens"),
        (),
    ),
    "reasoning_tokens": (
        ("reasoning_tokens", "thoughtsTokenCount"),
        (("completion_tokens_details", "reasoning_tokens"), ("output_tokens_details", "reasoning_tokens")),
    ),
}


def normalize_usage(raw: Any) -> dict[str, Any]:
    """Copy of a wire ``usage`` object plus the shared names every protocol reports.

    ``cached_tokens`` (prompt tokens served from a cache), ``cache_write_tokens``
    (prompt tokens written to a cache) and ``reasoning_tokens`` (thinking tokens)
    follow the Open Responses names. A field is added only when the wire reports it.
    The original wire keys stay in place.
    """
    if not isinstance(raw, dict):
        return {}
    out = dict(raw)
    for name, (flat, nested) in _USAGE_FIELDS.items():
        value = next((raw[key] for key in flat if raw.get(key) is not None), None)
        if value is None:
            for parent, key in nested:
                detail = raw.get(parent)
                if isinstance(detail, dict) and detail.get(key) is not None:
                    value = detail[key]
                    break
        if value is not None:
            out[name] = int(value)
    return out


def catalog_supports(model: str, name: str) -> bool:
    """False only when the model catalog lists ``name`` for ``model`` as unsupported; unknown models allow it."""
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    info = default_model_catalog().get(model)
    return info is None or info.supports(name)


def _catalog_knows(model: str) -> bool:
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    return default_model_catalog().get(model) is not None


def sampling_value(sampling: dict[str, Any] | None, model: str, name: str) -> Any:
    """An explicit sampling value, bounded by the catalog, or ``None`` when absent or unsupported for the model."""
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    if not sampling or sampling.get(name) is None:
        return None
    info = default_model_catalog().get(model)
    if info is None:
        return sampling[name]
    return info.clamp(name, sampling[name]) if info.supports(name) else None


def cache_breakpoints(
    sampling: dict[str, Any] | None, model: str, *, default_unknown: bool = True
) -> tuple[bool, bool, int]:
    """``(system, tools, messages)`` prompt-cache markers to place, from ``prompt_cache_breakpoints``.

    Absent means all three (two user turns). ``false`` means none. An object
    picks parts and an omitted part is off. A model the catalog marks
    unsupported gets none. ``default_unknown=False`` withholds the default for
    a model the catalog does not list (an explicit setting still applies).
    """
    if not catalog_supports(model, "prompt_cache_breakpoints"):
        return False, False, 0
    raw = (sampling or {}).get("prompt_cache_breakpoints")
    if raw is None:
        return (True, True, 2) if default_unknown or _catalog_knows(model) else (False, False, 0)
    if isinstance(raw, dict):
        return bool(raw.get("system")), bool(raw.get("tools")), max(0, min(2, int(raw.get("messages") or 0)))
    return False, False, 0


def cache_ttl(sampling: dict[str, Any] | None, model: str) -> str | None:
    """``"1h"`` when ``prompt_cache_retention`` is ``1h`` or ``24h``, else ``None`` (the 5 minute default)."""
    retention = (sampling or {}).get("prompt_cache_retention")
    if retention in {"1h", "24h"} and catalog_supports(model, "prompt_cache_retention"):
        return "1h"
    return None


class HttpLlmProtocol(LayerLifecycle):
    """Shared base of the http protocol layers: key, client, streaming failures, structured result."""

    protocol_layer = True
    per = "destination"
    call_kinds = ("llm",)
    priority = 0
    provider_id = ""
    kind = ""
    default_api_base = ""
    default_secret: SecretRef | None = None
    default_timeout = 120.0

    def __init__(
        self,
        *,
        api_base: str | None = None,
        api_key: SecretRef | dict[str, Any] | None = None,
        secret_resolver: SecretResolver | None = None,
        llm_proxy: dict[str, Any] | None = None,
        stream: bool = False,
        reasoning_effort: str | None = None,
        reasoning: ReasoningSettings | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        http_timeout: float | None = None,
        inner: Any = None,
        **_: Any,
    ) -> None:
        super().__init__()
        self.api_base = api_base or self.default_api_base
        self.api_key = self._coerce_api_key(api_key)
        self._secret_resolver = secret_resolver
        self.llm_proxy = llm_proxy
        self.stream = stream
        self.reasoning = coerce_reasoning_settings(reasoning, effort=reasoning_effort)
        self.reasoning_effort = self.reasoning.effort
        self.extra_body = dict(extra_body or {})
        self.http_timeout = http_timeout
        self.inner = inner
        self._aclient: httpx.AsyncClient | None = None

    def _coerce_api_key(self, api_key: SecretRef | dict[str, Any] | None) -> SecretRef | None:
        """The leaf's SecretRef; absent is the class's default secret, only when ``api_base`` is the class's own
        default endpoint (never sent to another host)."""
        if api_key is None or api_key == "":
            from mas.runtime.registry.llm_provider_registry import default_secret_for

            return default_secret_for(type(self), self.api_base)
        if isinstance(api_key, str):
            raise SecretRefError(
                "api_key must be a SecretRef object, not a string. "
                "Write {secret: NAME} or {secret: {name: NAME, key: value}}."
            )
        if isinstance(api_key, SecretRef):
            return api_key
        return parse_secret_ref(api_key, field="api_key")

    def protocol_params(self) -> dict[str, Any]:
        """Flat proxy fields, plus nested ``proxy`` when the spec kept that shape."""
        raw = self.llm_proxy if isinstance(self.llm_proxy, dict) else {}
        nested = raw.get("proxy") if isinstance(raw.get("proxy"), dict) else {}
        return {**raw, **nested}

    def _timeout(self) -> float:
        proxy = self.protocol_params()
        if proxy.get("timeout") is not None:
            return float(proxy["timeout"])
        if self.http_timeout is not None:
            return float(self.http_timeout)
        return float(self.default_timeout)

    def _get_async_client(self) -> Any:
        """The http layer's async client when this protocol sits on one, else a keep-alive pool."""
        if self.inner is not None:
            return self.inner.aclient
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(
                timeout=self._timeout(),
                verify=resolve_ssl_verify(self.llm_proxy),
                **httpx_client_kwargs_for(self.api_base),
            )
        return self._aclient

    async def _adiscard_client_after_transport_failure(self, exc: BaseException) -> None:
        if not isinstance(exc, httpx.TransportError):
            return
        if self.inner is not None:
            await self.inner.adiscard_aclient()
        if self._aclient is None:
            return
        client, self._aclient = self._aclient, None
        with contextlib.suppress(Exception):
            await client.aclose()

    async def _areraise_stream_failure(self, exc: BaseException, *, emitted_any: bool) -> None:
        await self._adiscard_client_after_transport_failure(exc)
        if emitted_any:
            classified = classify_llm_failure(exc)
            raise ClassifiedFailure(
                f"stream failed after output was already shown ({classified.code})",
                failure_class=FailureClass.APPLICATION,
                code="STREAM_PARTIAL",
            ) from None
        raise exc

    @classmethod
    def wrap(
        cls,
        inner: Any,
        params: dict[str, Any] | None = None,
        *,
        call_kinds: tuple[str, ...] | None = None,
    ) -> "HttpLlmProtocol":
        payload = dict(params or {})
        payload["inner"] = inner
        wrapped = cls.from_infra_spec(payload)
        if call_kinds is not None:
            wrapped.call_kinds = call_kinds
        return wrapped

    @classmethod
    def protocol_layers(cls, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
        return protocol_http_steps(cls.kind or cls.provider_id, spec, default_timeout=cls.default_timeout)

    @classmethod
    def from_provider_spec(cls, spec: dict[str, Any]) -> "HttpLlmProtocol":
        return cls.from_infra_spec(spec)

    @classmethod
    def from_infra_spec(cls, spec: dict[str, Any]) -> "HttpLlmProtocol":
        proxy = spec.get("proxy") if isinstance(spec.get("proxy"), dict) else spec
        llm_proxy = spec.get("llm_proxy") if isinstance(spec.get("llm_proxy"), dict) else spec
        timeout = None
        for src in (proxy, spec, llm_proxy):
            if isinstance(src, dict) and src.get("timeout") is not None:
                timeout = float(src["timeout"])
                break
        return cls(
            api_base=str(proxy.get("api_base") or spec.get("api_base") or spec.get("url") or cls.default_api_base),
            api_key=_secret_ref(spec, proxy, llm_proxy if isinstance(llm_proxy, dict) else None),
            secret_resolver=spec.get("secret_resolver"),
            llm_proxy=llm_proxy if isinstance(llm_proxy, dict) else spec,
            stream=bool(spec.get("stream", False)),
            reasoning=spec.get("reasoning"),
            reasoning_effort=str(spec["reasoning_effort"]) if spec.get("reasoning_effort") else None,
            extra_body=spec.get("extra") if isinstance(spec.get("extra"), dict) else None,
            http_timeout=timeout,
            inner=spec.get("inner"),
        )

    async def ainvoke(self, io: Any) -> Any:
        from mas.runtime.engine.infra_pipeline import _PROVIDER_CALL, _provider_result_return

        payload = _PROVIDER_CALL.get()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self.kind or 'llm'} layer invoked without a chat_completion in flight")
        call = {key: value for key, value in payload.items() if key != "_result"}
        return _provider_result_return(io, payload, await self.achat_completion(**call))

    def _auth_header(self) -> tuple[str, str]:
        """``(header name, value prefix)`` the key travels in."""
        return "Authorization", "Bearer "

    def _key_headers(self) -> dict[str, str]:
        """The auth header for this request, the key revealed here (none while an optional key is not set).

        A key goes only over https or to a loopback host; otherwise the call fails and nothing is sent.
        """
        ref = self.api_key
        if ref is None:
            raise RuntimeError(
                f"Missing API key for live LLM at {shown_urls(self.api_base)!r}: set api_key to a SecretRef "
                "({secret: {name: NAME, optional: true}} for a server that may take none); a default "
                "secret is only sent to the provider's own endpoint."
            )
        name, prefix = self._auth_header()
        try:
            revealed = reveal_headers_for(
                self.api_base,
                {name: ref},
                what="API key",
                resolver=self._secret_resolver or current_resolver(),
            )
        except PlainHttpRefused as exc:
            raise ClassifiedFailure(
                f"{self.kind or 'llm'} layer: {exc}", failure_class=FailureClass.FATAL, code="PLAIN_HTTP_KEY"
            ) from None
        except SecretMissing as exc:
            raise RuntimeError(f"Missing API key for live LLM: {exc}") from exc
        return {name: prefix + revealed[name]} if name in revealed else {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(kind={self.kind!r}, api_base={self.api_base!r})"

    async def achat_completion(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._complete(**kwargs)
        except ClassifiedFailure as failure:
            # A provider exception (its response, url and body) never travels with the failure.
            failure.__cause__ = failure.__context__ = None
            raise
        except Exception as exc:
            logger.debug("%s LLM call failed: %s", self.kind or "protocol", safe_exception_text(exc))
            classified = classify_llm_failure(exc)
            failure = ClassifiedFailure(
                classify_llm_http_error(exc),
                failure_class=classified.failure_class,
                code=classified.code,
                retry_after_s=classified.retry_after_s,
            )
        raise failure

    async def _complete(self, **kwargs: Any) -> dict[str, Any]:
        use_stream = self.stream if kwargs.get("stream") is None else bool(kwargs.get("stream"))
        settings = coerce_reasoning_settings(
            kwargs["reasoning"] if kwargs.get("reasoning") is not None else self.reasoning,
            effort=kwargs.get("reasoning_effort"),
        )
        return await self._apost_chat(
            model=kwargs.get("model") or "",
            messages=list(kwargs.get("messages") or []),
            tools=kwargs.get("tools"),
            temperature=0.7 if kwargs.get("temperature") is None else kwargs["temperature"],
            max_tokens=kwargs.get("max_tokens"),
            max_completion_tokens=kwargs.get("max_completion_tokens"),
            stream=use_stream,
            on_stream_chunk=kwargs.get("on_stream_chunk"),
            reasoning=settings,
            extra_body=kwargs.get("extra_body"),
            extra_headers=kwargs.get("extra_headers"),
            extra_query=kwargs.get("extra_query"),
            sampling=kwargs.get("sampling"),
            tool_choice=kwargs.get("tool_choice"),
            stream_options=kwargs.get("stream_options"),
            response_format=kwargs.get("response_format"),
            **({"reasoning_stream": kwargs["reasoning_stream"]} if kwargs.get("reasoning_stream") is not None else {}),
        )

    def _build_chat_request(self, **kwargs: Any) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        raise NotImplementedError

    def _message_from_completion(
        self, data: dict[str, Any], reasoning: ReasoningSettings, **extra: Any
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _structured_message(self, message: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        schema = schema_from_format(payload.get("response_format"))
        config = payload.get("output_config")
        if schema is None and isinstance(config, dict):
            schema = schema_from_format(config.get("format"))
        if schema is None:
            gen = payload.get("generationConfig")
            if isinstance(gen, dict) and isinstance(gen.get("responseJsonSchema"), dict):
                schema = gen["responseJsonSchema"]
            elif isinstance(gen, dict) and isinstance(gen.get("responseSchema"), dict):
                schema = gen["responseSchema"]
        return apply_structured_result(message, schema)

    async def _apost_chat(self, **kwargs: Any) -> dict[str, Any]:
        url, payload, headers, query = self._build_chat_request(**kwargs)
        reasoning = kwargs["reasoning"]
        extra = {key: kwargs[key] for key in ("reasoning_stream",) if kwargs.get(key) is not None}
        if kwargs.get("stream"):
            message = await self._achat_completion_streamed(
                url,
                payload,
                headers,
                on_stream_chunk=kwargs.get("on_stream_chunk"),
                reasoning=reasoning,
                params=query,
                **extra,
            )
            return self._structured_message(message, payload)
        resp = await arequest_once(
            self._get_async_client(),
            "POST",
            url,
            json=payload,
            headers=headers,
            params=query,
            get_client=self._get_async_client,
            on_failure=self._adiscard_client_after_transport_failure,
        )
        resp.raise_for_status()
        return self._structured_message(self._message_from_completion(resp.json(), reasoning, **extra), payload)

    async def _achat_completion_streamed(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        on_stream_chunk: Callable[[str], None] | None,
        reasoning: ReasoningSettings,
        params: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError


async def araise_for_status(resp: httpx.Response) -> None:
    """``raise_for_status`` for a streamed response: read an error body first so the failure can be classified."""
    if resp.is_error:
        await resp.aread()
    resp.raise_for_status()


def _secret_ref(*sources: dict[str, Any] | None) -> SecretRef | None:
    for src in sources:
        if not isinstance(src, dict) or src.get("api_key") in (None, ""):
            continue
        raw = src["api_key"]
        if isinstance(raw, SecretRef):
            return raw
        return parse_secret_ref(raw, field="api_key")
    return None
