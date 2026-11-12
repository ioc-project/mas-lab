#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra access pipeline — middleware around LLM and tool calls.

``llm_cache`` is common: one engine wrap outside the model router.
Every other LLM step is instantiated in front of each routed provider.
Tool steps use the same ``for`` / ``per`` stamps beside ``PipedLLMProvider``.

Implementations register as ``type: layer``.
The composer lives in ``mas.runtime.layers``. This module keeps the
bidirectional reply facade and the leaf wrappers — no plugin class names.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from mas.runtime.layers.compose import (
    apply_layer as apply_middleware,
)
from mas.runtime.layers.compose import (
    filter_pipeline_for_kind,
    split_pipeline_layers,
    wrap_call_stack,
)
from mas.runtime.layers.compose import (
    is_cache_layer as is_cache_middleware,
)
from mas.runtime.layers.compose import (
    is_scheduler_layer as is_scheduler_middleware,
)
from mas.runtime.layers.compose import (
    is_shared_layer as is_shared_middleware,
)
from mas.runtime.layers.compose import (
    layer_call_kinds as middleware_call_kinds,
)
from mas.runtime.layers.compose import (
    layer_id as middleware_id,
)
from mas.runtime.layers.compose import (
    layer_per as middleware_per,
)
from mas.runtime.layers.compose import (
    stamp_layer_step as stamp_middleware_step,
)
from mas.runtime.layers.lifecycle import Lifecycle
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

__all__ = [
    "apply_middleware",
    "filter_pipeline_for_kind",
    "is_cache_middleware",
    "is_scheduler_middleware",
    "is_shared_middleware",
    "middleware_call_kinds",
    "middleware_id",
    "middleware_per",
    "split_pipeline_layers",
    "stamp_middleware_step",
    "wrap_call_stack",
]

_PROVIDER_CALL: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mas_infra_provider_call",
    default=None,
)


class CallIdentity(Protocol):
    """Identity of the LLM call in flight. The dispatch port and leaf wrapper own it."""

    owns_call_identity: ClassVar[bool]

    @property
    def model(self) -> str: ...

    @property
    def api_base(self) -> str: ...

    @property
    def llm_proxies(self) -> dict[str, dict[str, Any]] | None: ...

    @property
    def llm_proxy(self) -> dict[str, Any] | None: ...

    @property
    def ctx(self) -> Any: ...


class InfraMiddleware(Protocol):
    """One named step on an LLM call. Inner is the next step, not the public engine."""

    middleware_id: str
    per: str
    stores_llm_responses: bool
    inner: Any

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn: ...

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn: ...

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str: ...


def infra_has_cache_step(
    pipeline: list[dict[str, Any]] | None,
    leaves: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """True when ``llm_cache`` is in the common wrap or any leaf pipeline."""
    for step in pipeline or []:
        if isinstance(step, dict) and is_cache_middleware(step):
            return True
    for leaf in (leaves or {}).values():
        for step in (leaf or {}).get("pipeline") or []:
            if isinstance(step, dict) and is_cache_middleware(step):
                return True
    return False


def innermost_call(engine: Any) -> Any:
    """The invoke body that owns call identity (dispatch port or leaf wrapper).

    Walk middleware ``.inner`` links. Stop at the object that publishes
    ``model`` / ``api_base`` for this call. Do not continue into the
    protocol plugin: that object has no in-flight model.
    """
    from mas.runtime.contracts.tool_semantics import existing_attr

    current = engine
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(type(current), "owns_call_identity", False):
            return current
        inner = existing_attr(current, "inner")
        if inner is None or inner is current:
            return current
        current = inner
    return current


def _identity(engine: Any) -> CallIdentity | None:
    source = innermost_call(engine)
    if getattr(type(source), "owns_call_identity", False):
        return source
    return None


def call_model(engine: Any) -> str:
    ident = _identity(engine)
    if ident is not None:
        return str(ident.model or "")
    source = innermost_call(engine)
    return str(getattr(source, "model", "") or "")


def call_api_base(engine: Any) -> str:
    ident = _identity(engine)
    if ident is not None:
        return str(ident.api_base or "")
    source = innermost_call(engine)
    return str(getattr(source, "api_base", "") or getattr(source, "base_url", "") or "")


def call_proxies(engine: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    ident = _identity(engine)
    if ident is not None:
        return dict(ident.llm_proxies or {}), dict(ident.llm_proxy or {})
    source = innermost_call(engine)
    return dict(getattr(source, "llm_proxies", None) or {}), dict(getattr(source, "llm_proxy", None) or {})


def call_ctx(engine: Any) -> Any:
    ident = _identity(engine)
    if ident is not None:
        return ident.ctx
    source = innermost_call(engine)
    return getattr(source, "ctx", None)


def wrap_llm_provider_pipeline(provider: Any, pipeline: list[dict[str, Any]] | None) -> Any:
    """Instantiate *pipeline* in front of one routed protocol plugin.

    The model router registers the returned object. Cache does not belong
    here — wrap the engine with :func:`split_pipeline_layers` common steps.
    Tool steps are dropped; they wrap :class:`PipedToolCall` instead.
    """
    steps = filter_pipeline_for_kind(
        [dict(step) for step in (pipeline or []) if isinstance(step, dict)],
        "llm",
    )
    if not steps or provider is None:
        return provider
    return PipedLLMProvider(inner=provider, pipeline=steps)


def wrap_tool_pipeline(body: Any, pipeline: list[dict[str, Any]] | None) -> Any:
    """Instantiate tool-kind *pipeline* in front of one tool invoke body."""
    steps = filter_pipeline_for_kind(
        [dict(step) for step in (pipeline or []) if isinstance(step, dict)],
        "tool",
    )
    if not steps or body is None:
        return body
    return PipedToolCall(inner=body, pipeline=steps)


@dataclass
class BidirectionalPipelineEngine(Lifecycle):
    """Engine facade aligned with ctl ``BidirectionalInfraPipeline`` reply pass."""

    inner: Any
    pipeline_steps: list[dict[str, Any]]

    def start(self) -> None:
        from mas.runtime.layers.lifecycle import call_start

        call_start(self.inner)

    def tick(self) -> None:
        from mas.runtime.layers.lifecycle import call_tick

        call_tick(self.inner)

    def stop(self) -> None:
        from mas.runtime.layers.lifecycle import call_stop

        call_stop(self.inner)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return str(self.inner.exchange_preview(op, correlation_id=correlation_id) or "")

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        ret = self.inner.invoke(io)
        if io.op != "LLM_CALL" or ret.response_kind != "MODEL_TEXT" or not ret.text:
            return ret
        from mas.runtime.engine.infra_chain import BidirectionalInfraPipeline, InfraChainContext

        chain = BidirectionalInfraPipeline.from_pipeline_steps(self.pipeline_steps)
        ctx = InfraChainContext(
            query={"content": ret.text, "correlation_id": io.correlation_id},
            correlation_id=io.correlation_id,
            target="LLM_CALL",
        )
        out = chain.backward_reply(ctx, {"content": ret.text})
        new_text = str(out.get("content") or ret.text)
        if new_text == ret.text:
            return ret
        return ret.model_copy(update={"text": new_text})

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        ret = await self.inner.ainvoke(io)
        if io.op != "LLM_CALL" or ret.response_kind != "MODEL_TEXT" or not ret.text:
            return ret
        from mas.runtime.engine.infra_chain import BidirectionalInfraPipeline, InfraChainContext

        chain = BidirectionalInfraPipeline.from_pipeline_steps(self.pipeline_steps)
        ctx = InfraChainContext(
            query={"content": ret.text, "correlation_id": io.correlation_id},
            correlation_id=io.correlation_id,
            target="LLM_CALL",
        )
        out = chain.backward_reply(ctx, {"content": ret.text})
        new_text = str(out.get("content") or ret.text)
        if new_text == ret.text:
            return ret
        return ret.model_copy(update={"text": new_text})


def _provider_call_payload() -> dict[str, Any]:
    payload = _PROVIDER_CALL.get()
    if not isinstance(payload, dict):
        raise RuntimeError("infra provider pipeline invoked without a chat_completion in flight")
    return payload


@dataclass
class _ProviderCallEngine:
    """Present ``chat_completion`` as ``EngineContract.invoke`` for per-leaf middleware."""

    owns_call_identity: ClassVar[bool] = True
    inner: Any
    api_base: str = ""
    kind: str = ""
    provider_id: str = ""

    @property
    def model(self) -> str:
        payload = _PROVIDER_CALL.get() or {}
        return str(payload.get("model") or "")

    @property
    def llm_proxies(self) -> dict[str, dict[str, Any]] | None:
        return None

    @property
    def llm_proxy(self) -> dict[str, Any] | None:
        return None

    @property
    def ctx(self) -> Any:
        return None

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        del correlation_id
        payload = _PROVIDER_CALL.get() or {}
        return f"provider:{op}:{payload.get('model') or ''}"

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        payload = _provider_call_payload()
        result = self.inner.chat_completion(**payload)
        payload["_result"] = result
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            model=str(payload.get("model") or ""),
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        payload = _provider_call_payload()
        result = await self.inner.achat_completion(**payload)
        payload["_result"] = result
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            model=str(payload.get("model") or ""),
        )


@dataclass
class PipedLLMProvider(Lifecycle):
    """One leaf's infra pipeline wrapping a protocol plugin.

    The registry routes ``model=`` here; ``chat_completion`` runs that leaf's
    middleware (scheduler, fault_inject, …) then the inner provider.
    """

    inner: Any
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    _leaf: _ProviderCallEngine = field(init=False, repr=False)
    _stack: Any = field(init=False, repr=False)
    kind: str = ""
    provider_id: str = ""
    api_base: str = ""
    api_key_env: str = ""

    def __post_init__(self) -> None:
        inner = self.inner
        self.kind = str(getattr(inner, "kind", None) or getattr(inner, "provider_id", None) or self.kind or "")
        self.provider_id = str(getattr(inner, "provider_id", None) or self.kind)
        self.api_base = str(getattr(inner, "api_base", None) or self.api_base or "")
        self.api_key_env = str(getattr(inner, "api_key_env", None) or self.api_key_env or "")
        self._leaf = _ProviderCallEngine(
            inner=inner,
            api_base=self.api_base,
            kind=self.kind,
            provider_id=self.provider_id,
        )
        self._stack = wrap_call_stack(self._leaf, list(self.pipeline))

    def stop(self) -> None:
        from mas.runtime.layers.lifecycle import call_stop

        call_stop(self._stack)

    def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        return self._run_sync(dict(kwargs))

    async def achat_completion(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run_async(dict(kwargs))

    def _run_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = _PROVIDER_CALL.set(payload)
        try:
            ret = self._stack.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
            return _completion_from_pipeline(ret, payload)
        finally:
            _PROVIDER_CALL.reset(token)

    async def _run_async(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = _PROVIDER_CALL.set(payload)
        try:
            ret = await self._stack.ainvoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
            return _completion_from_pipeline(ret, payload)
        finally:
            _PROVIDER_CALL.reset(token)


@dataclass
class PipedToolCall(Lifecycle):
    """One destination's infra pipeline wrapping a tool invoke body.

    Same shape as :class:`PipedLLMProvider`, for ``for: tool`` steps. The
    engine's common tool wrap uses :func:`wrap_call_stack`; named tool
    servers clone destination steps the way LLM leaves clone schedulers.
    """

    inner: Any
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    _stack: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._stack = wrap_call_stack(self.inner, list(self.pipeline))

    def tick(self) -> None:
        from mas.runtime.layers.lifecycle import call_tick

        call_tick(self._stack)

    def stop(self) -> None:
        from mas.runtime.layers.lifecycle import call_stop

        call_stop(self._stack)

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self._stack.invoke(io)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return await self._stack.ainvoke(io)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return str(self._stack.exchange_preview(op, correlation_id=correlation_id) or "")


def _completion_from_pipeline(ret: EngineIoReturn, payload: dict[str, Any]) -> dict[str, Any]:
    if ret.response_kind == "ERROR":
        raise RuntimeError(ret.text or "infra pipeline rejected LLM_CALL")
    result = payload.get("_result")
    if not isinstance(result, dict):
        raise RuntimeError("infra pipeline returned no completion")
    return result
