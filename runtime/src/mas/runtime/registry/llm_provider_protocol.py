#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Protocol for pluggable LLM wire-protocol providers."""

from __future__ import annotations

import inspect
from typing import Any, Protocol, runtime_checkable


class MissingLLMProviderContract(TypeError):
    """Raised when an ``llm_provider`` plugin has neither ``achat_completion`` nor ``chat_completion``."""


MissingLLMProviderAsyncContract = MissingLLMProviderContract


def _is_async(method: Any) -> bool:
    fn = method.__func__ if inspect.ismethod(method) else method
    return inspect.iscoroutinefunction(fn)


def require_achat_completion(provider: Any, *, name: str = "") -> None:
    """Reject a provider class or instance that has no completion method at all.

    ``achat_completion`` is the form to write. A provider that only has the sync ``chat_completion`` is
    still accepted: :func:`adapt_llm_provider` runs it on a worker thread and warns once.
    """
    label = (
        name
        or getattr(provider, "urn", None)
        or getattr(provider, "provider_id", None)
        or getattr(provider, "__name__", None)
        or type(provider).__name__
    )
    amethod = getattr(provider, "achat_completion", None)
    if callable(amethod):
        if not _is_async(amethod):
            raise MissingLLMProviderContract(
                f"llm_provider {label!r} defines achat_completion but it is not async "
                "(must be `async def achat_completion`)."
            )
        return
    if not callable(getattr(provider, "chat_completion", None)):
        raise MissingLLMProviderContract(
            f"llm_provider {label!r} must define `async def achat_completion` (or the sync chat_completion)."
        )


class SyncLLMProvider:
    """A provider that only has ``chat_completion``, awaited through the worker threads."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def achat_completion(self, **kwargs: Any) -> dict[str, Any]:
        from mas.runtime.aio import run_sync, warn_sync_plugin

        warn_sync_plugin(self._provider, "chat_completion", "achat_completion")
        return await run_sync(self._provider.chat_completion, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name == "_provider":  # not set yet (copy, unpickle): do not recurse
            raise AttributeError(name)
        return getattr(self._provider, name)


def adapt_llm_provider(provider: Any, *, name: str = "") -> Any:
    """The provider the engine awaits: itself when async, else wrapped around its sync method."""
    require_achat_completion(provider, name=name)
    if callable(getattr(provider, "achat_completion", None)):
        return provider
    return SyncLLMProvider(provider)


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM-provider plugins in the MAS runtime.

    Runtime calling is a chat-completion. A plugin speaks one wire protocol
    (OpenAI-compatible HTTP, later Bedrock, …). Cache is a wrapper around a
    routed protocol plugin, not a protocol. Offline CI replays a recorded
    live provider through ``llm_cache`` (``raise_on_miss``).
    The engine assembles messages and parses the returned OpenAI-shaped
    assistant message; it does not own vendor HTTP.

    ``achat_completion`` is what to write: the engine awaits it directly. A provider that only
    implements the sync ``chat_completion`` still works; it runs on the worker threads
    (``engine.sync_threads``) and the runtime warns once.

    Optional leaf identity: ``kind``, ``api_base``, ``api_key_env``. A
    provider that omits them still runs; the leaf pipeline treats missing
    values as empty.
    """

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
        """Return an OpenAI-shaped assistant ``message`` dict.

        ``max_tokens`` / ``max_completion_tokens`` (kwarg): output budget;
        send at most one, and none when both are ``None`` (server default).
        Optional kwargs:
        **static (also on spec.models[])** — ``reasoning`` / ``reasoning_effort``,
        ``sampling``, ``extra_body``.
        **dynamic (per call)** — ``stream``, ``stream_options``, ``on_stream_chunk``,
        ``api_key``, ``tool_choice``, ``extra_headers``, ``extra_query``,
        ``reasoning_stream`` (a ``ReasoningStream``: call ``feed_delta`` for each
        stream delta and ``close`` at the end of the stream, or ``received`` with
        the reasoning text of a whole response, so the reasoning interval is
        reported when it happens).
        """
        ...
