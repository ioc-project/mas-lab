#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Cache LLM provider — wraps another LLMProvider (read-through / write-through)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.runtime.engine.llm_cache import (
    assistant_message_from_cache_content,
    completion_cache_params,
    llm_cache_key,
    load_cache,
    lookup_response,
    persist_cache,
    resolve_cache_path,
)


class CacheLLMProvider:
    """LLMProvider decorator: disk cache in front of an inner protocol plugin.

    Independent ``allow_read`` / ``allow_write`` match the engine cache policy
    and the infra ``llm_cache`` middleware knobs. This wraps *chat completions*,
    not ``EngineContract.invoke`` — preview-keyed EngineIoReturn replay stays
    on ``LlmCacheMiddleware``.
    """

    provider_id = "cache"
    kind = "cache"

    def __init__(
        self,
        inner: Any,
        *,
        cache_path: str | Path | None = None,
        allow_read: bool = True,
        allow_write: bool = True,
        raise_on_miss: bool = False,
        **_: Any,
    ) -> None:
        if inner is None:
            raise ValueError("CacheLLMProvider requires an inner LLMProvider")
        self.inner = inner
        self.cache_path = resolve_cache_path(cache_path) if cache_path or allow_read or allow_write else None
        self.allow_read = allow_read
        self.allow_write = allow_write
        self.raise_on_miss = raise_on_miss
        # Load regardless of allow_read so a write-only run merges onto disk
        # instead of persist_cache's full-file rewrite wiping prior entries.
        self._cache: dict[str, Any] = load_cache(self.cache_path) if self.cache_path else {}

    @classmethod
    def from_provider_spec(cls, spec: dict[str, Any]) -> "CacheLLMProvider":
        return cls.from_infra_spec(spec)

    @classmethod
    def from_infra_spec(cls, spec: dict[str, Any]) -> "CacheLLMProvider":
        inner = spec.get("inner")
        params = dict(spec.get("params") or {})
        path = spec.get("cache_path", params.get("cache_path"))
        return cls(
            inner,
            cache_path=path,
            allow_read=spec.get("allow_read", params.get("allow_read", True)) is not False,
            allow_write=spec.get("allow_write", params.get("allow_write", True)) is not False,
            raise_on_miss=spec.get("raise_on_miss", params.get("raise_on_miss", False)) is True,
        )

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = completion_cache_params(temperature=temperature, max_tokens=max_tokens, **kwargs)
        if self.allow_read:
            content, cached_usage, _source = lookup_response(
                self._cache, model, messages, tools=tools, params=params
            )
            cached = assistant_message_from_cache_content(content)
            if cached is not None:
                if cached_usage:
                    cached["usage"] = cached_usage
                cached["_mas_cache_status"] = "hit"
                return cached
            if self.raise_on_miss:
                key = llm_cache_key(model, messages, tools, params=params)
                raise RuntimeError(
                    f"llm_cache miss (raise_on_miss=true) for key {key}: no recorded answer for this prompt. "
                    "To call the model, turn read-only mode off (no --cache-read-only, no `cache: read-only` in config.yaml) and set an API key."
                )
        message = self.inner.chat_completion(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if self.allow_read:
            message = dict(message)
            message["_mas_cache_status"] = "miss"
        if self.allow_write and self.cache_path:
            self._persist_message(model, messages, tools, message, params=params)
        return message

    async def achat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = completion_cache_params(temperature=temperature, max_tokens=max_tokens, **kwargs)
        if self.allow_read:
            content, cached_usage, _source = lookup_response(
                self._cache, model, messages, tools=tools, params=params
            )
            cached = assistant_message_from_cache_content(content)
            if cached is not None:
                if cached_usage:
                    cached["usage"] = cached_usage
                cached["_mas_cache_status"] = "hit"
                return cached
            if self.raise_on_miss:
                key = llm_cache_key(model, messages, tools, params=params)
                raise RuntimeError(
                    f"llm_cache miss (raise_on_miss=true) for key {key}: no recorded answer for this prompt. "
                    "To call the model, turn read-only mode off (no --cache-read-only, no `cache: read-only` in config.yaml) and set an API key."
                )
        message = await self.inner.achat_completion(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if self.allow_read:
            message = dict(message)
            message["_mas_cache_status"] = "miss"
        if self.allow_write and self.cache_path:
            self._persist_message(model, messages, tools, message, params=params)
        return message

    def _persist_message(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        message: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
    ) -> None:
        cache_key = llm_cache_key(model, messages, tools, params=params)
        usage = message.get("usage") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            self._cache[cache_key] = {
                "tool_calls": tool_calls,
                "usage": usage,
                "source": "cache",
            }
            persist_cache(self.cache_path, self._cache)
            return
        text = str(message.get("content") or "").strip()
        if not text:
            return
        self._cache[cache_key] = {
            "content": text,
            "usage": usage,
            "source": "cache",
        }
        persist_cache(self.cache_path, self._cache)
