#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Protocol for pluggable LLM wire-protocol providers."""

from __future__ import annotations

import inspect
from typing import Any, Protocol, runtime_checkable


class MissingLLMProviderAsyncContract(TypeError):
    """Raised when an ``llm_provider`` plugin omits ``achat_completion``.

    The async engine awaits the provider directly on one cooperative event
    loop. A missing method must fail at load/register time — never by
    running ``chat_completion`` on a worker thread, which would break the
    lock-free shared-state argument for ``SpawnLedger``, ``InProcessCommBus``,
    and ``WorkingMemoryRegistry``.
    """


def require_achat_completion(provider: Any, *, name: str = "") -> None:
    """Reject a provider class or instance that lacks the async contract method."""
    label = (
        name
        or getattr(provider, "urn", None)
        or getattr(provider, "provider_id", None)
        or getattr(provider, "__name__", None)
        or type(provider).__name__
    )
    method = getattr(provider, "achat_completion", None)
    if not callable(method):
        raise MissingLLMProviderAsyncContract(
            f"llm_provider {label!r} is missing required method achat_completion "
            "(async twin of chat_completion). Silent worker-thread fallback is not supported."
        )
    fn = method.__func__ if inspect.ismethod(method) else method
    if not inspect.iscoroutinefunction(fn):
        raise MissingLLMProviderAsyncContract(
            f"llm_provider {label!r} defines achat_completion but it is not async "
            "(must be `async def achat_completion`, matching chat_completion)."
        )


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM-provider plugins in the MAS runtime.

    Runtime calling is a chat-completion. A plugin speaks one wire protocol
    (OpenAI-compatible HTTP, later Bedrock, …). Cache is a wrapper around a
    routed protocol plugin, not a protocol. Offline CI replays a recorded
    live provider through ``llm_cache`` (``raise_on_miss``).
    The engine assembles messages and parses the returned OpenAI-shaped
    assistant message; it does not own vendor HTTP.

    ``achat_completion`` is required — the plan-04 async path awaits it
    directly. Providers that only implement the sync method are rejected at
    plugin load / instantiation, not adapted onto a worker thread.

    Optional leaf identity: ``kind``, ``api_base``, ``api_key_env``. A
    provider that omits them still runs; the leaf pipeline treats missing
    values as empty.
    """

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
        """Return an OpenAI-shaped assistant ``message`` dict.

        ``max_tokens`` / ``max_completion_tokens`` (kwarg): output budget;
        send at most one, and none when both are ``None`` (server default).
        Optional kwargs:
        **static (also on spec.models[])** — ``reasoning`` / ``reasoning_effort``,
        ``sampling``, ``extra_body``.
        **dynamic (per call)** — ``stream``, ``stream_options``, ``on_stream_chunk``,
        ``api_key``, ``tool_choice``, ``extra_headers``, ``extra_query``.
        """
        ...

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
        """Async twin of :meth:`chat_completion` — same arguments and result shape."""
        ...
