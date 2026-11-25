#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live LLM engine — EngineContract over an LLMProvider plugin."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from mas.runtime.boundary.context.assemble import (
    assemble_llm_messages,
    has_tool_results,
    llm_request_tools,
)
from mas.runtime.boundary.gov.budget import BudgetTracker, budget_from_manifest
from mas.runtime.engine.exchange_preview import ExchangeSnapshot, format_exchange_snapshot, tool_def_names
from mas.runtime.engine.llm_output_limits import (
    OutputLimits,
    estimate_prompt_tokens,
    resolve_output_limits,
    sum_usage,
)
from mas.runtime.engine.llm_reasoning import (
    ReasoningSettings,
    ReasoningStream,
    coerce_reasoning_settings,
    reasoning_settings_from_manifest,
    reasoning_text,
    think_block_text,
)
from mas.runtime.engine.llm_request import extra_from_manifest, sampling_settings_from_manifest, sampling_temperature
from mas.runtime.engine.textual_tool_calls import maybe_recover_textual_tool_calls, repair_merged_arg_keys
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool, ToolExecutionError
from mas.runtime.engine.tools import openai_tools
from mas.runtime.layers.lifecycle import Lifecycle, acall_tick
from mas.runtime.layers.probe import PeerProber, ToolProber
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import classify_llm_failure, classify_llm_http_error
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

if TYPE_CHECKING:
    from mas.runtime.driver.mocks import AutoCtxAssembler

logger = logging.getLogger(__name__)

# The reasoning interval of the LLM call in progress (see LiveLlmEngine._reasoning_for).
_REASONING_STREAM: contextvars.ContextVar[ReasoningStream | None] = contextvars.ContextVar(
    "mas_llm_reasoning_stream", default=None
)


class TruncatedCompletionError(RuntimeError):
    """Raised when ``on_truncation: error`` and the completion hit its token budget."""


@dataclass
class _InfraDispatchPort(ToolProber, PeerProber):
    """Innermost LLM/tool body for infra steps. Listed members only; not a public engine."""

    owns_call_identity: ClassVar[bool] = True
    engine: LiveLlmEngine

    async def aprobe_tool(self, name: str, arguments: Mapping[str, Any]) -> None:
        await self.engine.aprobe_tool(name, arguments)

    async def aprobe_peer(self, name: str) -> bool | None:
        return await self.engine.aprobe_peer(name)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return await self.engine._adispatch(io)

    async def aexchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return await self.engine.aexchange_preview(op, correlation_id=correlation_id)

    @property
    def model(self) -> str:
        return self.engine.model

    @property
    def api_base(self) -> str:
        return self.engine.api_base

    @property
    def llm_proxy(self) -> dict[str, Any] | None:
        return self.engine.llm_proxy

    @property
    def llm_proxies(self) -> dict[str, dict[str, Any]] | None:
        return self.engine.llm_proxies

    @property
    def ctx(self) -> AutoCtxAssembler | None:
        return self.engine.ctx


@dataclass
class _ToolDestRouter:
    """Pick the dest wrap for the server that claims this tool, else dispatch."""

    engine: LiveLlmEngine
    destinations: dict[str, Any]
    servers: dict[str, dict[str, Any]]
    fallback: Any

    def _pending_name(self, correlation_id: int) -> str:
        in_flight = self.engine._in_flight()
        if in_flight is not None:
            return in_flight.name
        by_cid = self.engine._pending_tools_by_cid.get(correlation_id)
        if by_cid is not None:
            return str(by_cid[0] or "")
        return str(self.engine._pending_tool or "")

    def _advertised_by_server(self) -> dict[str, tuple[str, ...]]:
        from mas.runtime.engine.tool_routing import SYSTEM_TOOL_NAMES

        provider = self.engine.tool_provider
        raw = provider.advertised_names_by_provider() if provider is not None else {}
        buckets: dict[str, tuple[str, ...]] = {}
        for sid, names in raw.items():
            token = str(sid or "").strip()
            kept = tuple(
                name for name in (str(item).strip() for item in names or ()) if name and name not in SYSTEM_TOOL_NAMES
            )
            if token and kept:
                buckets[token] = kept
        return buckets

    def _owner(self, tool: str) -> str | None:
        from mas.runtime.engine.tool_routing import owner_for_tool, tool_server_claim

        advertised = self._advertised_by_server()
        claims = []
        for sid, server in self.servers.items():
            payload = dict(server or {})
            extra = advertised.get(sid)
            if extra and not payload.get("advertised"):
                payload["advertised"] = extra
            claims.append(tool_server_claim(sid, payload))
        return owner_for_tool(tool, claims)

    def _pick(self, correlation_id: int) -> Any:
        sid = self._owner(self._pending_name(correlation_id))
        if sid is not None:
            wrap = self.destinations.get(sid)
            if wrap is not None:
                return wrap
        return self.fallback

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return await self._pick(io.correlation_id).ainvoke(io)

    async def aexchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return str(await self._pick(correlation_id).aexchange_preview(op, correlation_id=correlation_id) or "")


@dataclass
class LiveLlmEngine(Lifecycle):
    """EngineContract implementation — delegates chat completions to LLMProvider plugins."""

    ctx: AutoCtxAssembler | None = None
    manifest: dict | None = None
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    # None sends no limit (server default). Reasoning models bill thinking
    # against the same budget, see llm_output_limits.
    max_tokens: int | None = None
    output_limits: OutputLimits | None = None
    reasoning_effort: str | None = None
    reasoning: ReasoningSettings | dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    cache_path: Path | None = None
    use_cache: bool = True
    cache_read: bool = True
    cache_write: bool = True
    stream: bool = False
    use_tool_loop: bool = False
    tool_placement: Any | None = None  # ToolPlacement from the RuntimeEngine; None = defaults
    llm_proxy: dict[str, Any] | None = None
    llm_proxies: dict[str, dict[str, Any]] | None = None
    http_timeout: float | None = None
    manifest_dir: Path | None = None
    delegation: Any | None = None
    engine_tool_contracts: tuple[Any, ...] = ()
    delegation_peer_descriptions: dict[str, str] | None = None
    tool_provider: Any | None = None
    llm_provider: Any | None = None
    _pending_tool: str = field(default="", init=False)
    _pending_tool_args: dict[str, Any] = field(default_factory=dict, init=False)
    _pending_tools_by_cid: dict[int, tuple[str, dict[str, Any]]] = field(default_factory=dict, init=False)
    _offered_tool_names: list[str] = field(default_factory=list, init=False)
    _budget: BudgetTracker = field(default_factory=BudgetTracker, init=False)
    _model_access: Any | None = field(default=None, init=False)
    _infra_stack: Any = field(default=None, init=False, repr=False)
    _tool_stack: Any = field(default=None, init=False, repr=False)
    _tool_destinations: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def set_scheduled_tool(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        """Kernel-scheduled tool (e.g. plan-execute) — TLA: ToolMachine.tla."""
        self._pending_tool = name
        self._pending_tool_args = dict(arguments or {})

    def set_tool_for_correlation(self, correlation_id: int, name: str, arguments: dict[str, Any] | None = None) -> None:
        self._pending_tools_by_cid[correlation_id] = (name, dict(arguments or {}))

    def __post_init__(self) -> None:
        # Resolve model name recursively through infra manifest mappings
        # (e.g., default -> haiku -> bedrock/anthropic.claude-haiku-...)
        infra_spec = (self.llm_proxy or {}).get("spec") or {}
        from mas.runtime.engine.llm_model_catalog import resolve_model_recursive

        self.model = resolve_model_recursive(self.model, infra_spec) or self.model

        self.reasoning = coerce_reasoning_settings(
            self.reasoning
            if self.reasoning is not None
            else reasoning_settings_from_manifest(self.manifest, model=self.model),
            effort=self.reasoning_effort,
        )
        self.reasoning_effort = self.reasoning.effort
        self.sampling = sampling_settings_from_manifest(self.manifest, model=self.model)
        if self.output_limits is None:
            self.output_limits = resolve_output_limits(
                self.manifest,
                model=self.model,
                generation=(self.llm_proxy or {}).get("generation"),
                max_tokens_override=self.max_tokens,
            )
        self.max_tokens = self.output_limits.max_tokens
        if self.extra_body is None:
            self.extra_body = extra_from_manifest(self.manifest, model=self.model)
        self._budget = budget_from_manifest(self.manifest)
        spec = ((self.manifest or {}).get("spec") or {}) if isinstance(self.manifest, dict) else {}
        if self.llm_provider is None:
            self.llm_provider = self._default_llm_provider()
        self.llm_provider = self._wrap_cache(self.llm_provider)
        # Tests that still inspect _model_access see the routed provider.
        self._model_access = self.llm_provider
        from mas.runtime.layers.control import resolve_call_pipeline

        self.install_tool_pipeline(resolve_call_pipeline(spec, [], kind="tool"))

    def install_infra_pipeline(self, pipeline: list[dict[str, Any]] | None) -> None:
        """Wrap this engine's LLM call with ``per: all`` steps only.

        Destination steps (scheduler, fault) wrap each leaf. Passing one
        here is an error; split the pipeline first.
        """
        from mas.runtime.engine.infra_pipeline import (
            middleware_id,
            split_pipeline_layers,
            wrap_call_stack,
        )
        from mas.runtime.layers.compose import observes_failures

        steps = [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
        common, per_leaf = split_pipeline_layers(steps)
        if per_leaf:
            names = [middleware_id(step) or "?" for step in per_leaf]
            raise ValueError(
                f"install_infra_pipeline only accepts per: all steps; got destination {names}. Wrap those on each leaf."
            )
        shared = [middleware_id(step) or "?" for step in common if observes_failures(step)]
        if shared:
            # The shared copy sits above the engine, which turns a provider failure into an ERROR return.
            raise ValueError(f"{shared} must wrap each destination (per: destination), not the shared engine call")
        if not common:
            self._infra_stack = None
            return
        self._infra_stack = wrap_call_stack(_InfraDispatchPort(self), common)

    def install_tool_pipeline(
        self,
        pipeline: list[dict[str, Any]] | None,
        *,
        destinations: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Wrap this engine's tool call with ``per: all`` ``for: tool`` steps.

        Destination tool steps wrap each tool server via ``PipedToolCall``.
        Passing a destination step in *pipeline* is an error; put those
        on *destinations* instead.
        """
        from mas.runtime.engine.infra_pipeline import (
            filter_pipeline_for_kind,
            layer_role,
            middleware_id,
            split_pipeline_layers,
            wrap_call_stack,
            wrap_tool_pipeline,
        )

        steps = filter_pipeline_for_kind(
            [dict(step) for step in (pipeline or []) if isinstance(step, dict)],
            "tool",
        )
        common, per_dest = split_pipeline_layers(steps)
        if per_dest:
            names = [middleware_id(step) or "?" for step in per_dest]
            raise ValueError(
                "install_tool_pipeline only accepts per: all steps; "
                f"got destination {names}. Wrap those on each tool server."
            )
        dispatch = _InfraDispatchPort(self)
        dest_wraps: dict[str, Any] = {}
        dest_servers = {str(name): dict(server or {}) for name, server in (destinations or {}).items()}
        for name, server in dest_servers.items():
            dest_steps = filter_pipeline_for_kind(
                [dict(step) for step in server.get("pipeline") or [] if isinstance(step, dict)],
                "tool",
            )
            _, dest_only = split_pipeline_layers(dest_steps)
            # The tool provider built its own transport and protocol layers; only wrap steps go around it.
            dest_only = [step for step in dest_only if layer_role(step) == "wrap"]
            if dest_only:
                dest_wraps[name] = wrap_tool_pipeline(dispatch, dest_only)
        self._tool_destinations = dest_wraps
        inner: Any = (
            _ToolDestRouter(
                engine=self,
                destinations=dest_wraps,
                servers=dest_servers,
                fallback=dispatch,
            )
            if dest_wraps
            else dispatch
        )
        if not common and not dest_wraps:
            self._tool_stack = None
            return
        if not common:
            self._tool_stack = inner
            return
        self._tool_stack = wrap_call_stack(inner, common)

    async def atick(self) -> None:
        """Periodic work of the tool layers; run before each LLM call."""
        await acall_tick(self._tool_stack)
        for wrap in self._tool_destinations.values():
            await acall_tick(wrap)

    async def aprobe_peer(self, name: str) -> bool | None:
        """Check a peer through the delegation's link. Not a model call: no budget, no layers."""
        probe = getattr(self.delegation, "aprobe", None)
        if not callable(probe):
            return None
        return await probe(name, ctx=self.ctx)

    async def aprobe_tool(self, name: str, arguments: Mapping[str, Any]) -> None:
        """Run a tool the running spec took away, to see whether it is back. Not a model call: no budget, no layers."""
        if self.tool_provider is None:
            raise ToolExecutionError(f"no tool provider to probe {name!r}")
        from mas.runtime.engine.tool_dispatch import tool_is_idempotent
        from mas.runtime.engine.tools import current_spec_for_advertise

        if not tool_is_idempotent(name, current_spec_for_advertise(self.ctx)):
            # A probe runs outside the model's turn; only a tool declared safe to repeat may be called that way.
            raise ToolExecutionError(f"tool {name!r} is not declared idempotent, so it cannot be a probe")
        await self.tool_provider.aprobe_tool(name, dict(arguments), ctx=self.ctx)

    def stop(self) -> None:
        from mas.runtime.layers.lifecycle import call_stop

        call_stop(self._tool_stack)
        call_stop(self._infra_stack)
        call_stop(self.llm_provider)
        call_stop(self.tool_provider)
        closer = getattr(self.tool_provider, "close", None)
        if callable(closer):
            closer()
        for wrap in self._tool_destinations.values():
            call_stop(wrap)

    def _default_llm_provider(self) -> Any:
        from mas.runtime.registry.llm_provider_registry import llm_provider_from_infra

        proxy = dict(self.llm_proxy or {})
        proxy.setdefault("api_base", self.api_base)
        if self.http_timeout is not None:
            proxy.setdefault("timeout", self.http_timeout)
        return llm_provider_from_infra(
            proxy,
            manifest=self.manifest,
            stream=self.stream,
            reasoning_effort=self.reasoning.effort,
            reasoning=self.reasoning.to_spec_dict(),
        )

    def _wrap_cache(self, provider: Any) -> Any:
        if not (self.use_cache and (self.cache_read or self.cache_write) and self.cache_path):
            return provider
        from mas.runtime.registry.llm_provider_registry import wrap_llm_provider_cache

        return wrap_llm_provider_cache(
            provider,
            cache_path=self.cache_path,
            cache_read=self.cache_read,
            cache_write=self.cache_write,
        )

    def reset_turn_state(self) -> None:
        self._pending_tool = ""
        self._pending_tool_args = {}
        self._pending_tools_by_cid.clear()
        self._offered_tool_names = []

    async def asummarize_messages(self, messages: list[dict[str, Any]], *, model: str | None = None) -> str:
        """One-off chat completion. The summarizer plugin builds the prompt."""
        if not self._budget.allow_llm():
            raise RuntimeError("history summarizer: LLM call budget exceeded (spec.budget.max_llm_calls)")
        self._budget.note_llm()
        use_model = (model or self.model or "").strip() or self.model
        logger.info(
            "history summarizer: completing %d message(s) model=%s%s",
            len(messages),
            use_model,
            " (override)" if model and model != self.model else "",
        )
        message, _retries = await self._acomplete_with_truncation_policy(
            messages, tools=None, temperature=0.0, model=use_model
        )
        content = message.get("content") if isinstance(message, dict) else ""
        from mas.runtime.contracts.content_types import text_of

        return text_of(content).strip()

    async def aexchange_snapshot(self, op: str, *, correlation_id: int = 0) -> ExchangeSnapshot:
        """Structured outbound LLM payload or pending tool call. Not a display string.

        ``correlation_id`` is this op's real id when the caller has one (a
        cache-key preview, an exchange-log entry) — assembling messages
        records context-assembly telemetry tagged with whatever id is set on
        ``ctx``, so a preview must pass the real id through rather than
        leaving the previous call's id in place (stale) or zeroing it
        (untraceable): both defeat mapping that telemetry back to its LLM
        call, which matters most on a cache hit, where this preview is the
        only assembly this op ever does.
        """
        if op == "LLM_CALL":
            if self.ctx is not None:
                self.ctx._assembly_correlation_id = correlation_id
            tool_defs = self._tool_defs()
            messages = await self._abuild_messages(tools=tool_defs)
            api_tools = llm_request_tools(messages, tools=tool_defs or None)
            tools_note = ""
            if tool_defs and api_tools is None and has_tool_results(messages):
                tools_note = "(omitted — answer-from-tool-result turn)"
            return ExchangeSnapshot(messages=messages, tools=api_tools, tools_note=tools_note)
        if op == "TOOL_CALL":
            return ExchangeSnapshot(
                tool_name=self._pending_tool or "tool",
                arguments=dict(self._pending_tool_args),
            )
        return ExchangeSnapshot()

    async def aexchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        """Pretty-print of the exchange snapshot (cache keys, tests). Not interchange."""
        return format_exchange_snapshot(await self.aexchange_snapshot(op, correlation_id=correlation_id))

    def _take_pending_tool(self, correlation_id: int) -> tuple[str, dict[str, Any]]:
        by_cid = self._pending_tools_by_cid.pop(correlation_id, None)
        if by_cid is not None:
            return str(by_cid[0] or "tool"), dict(by_cid[1] or {})
        tool = self._pending_tool or "tool"
        args = dict(self._pending_tool_args)
        self._pending_tool = ""
        self._pending_tool_args = {}
        return tool, args

    def _in_flight(self) -> InFlightTool | None:
        """The tool this engine is dispatching in this context. A parent engine's call is not ours."""
        current = IN_FLIGHT_TOOL.get()
        return current if current is not None and current.owner == id(self) else None

    def _begin_tool_call(self, io: InvokeEngineIo) -> contextvars.Token[InFlightTool | None] | None:
        if io.op != "TOOL_CALL":
            return None
        if not self._budget.allow_tool():
            return None
        if self._in_flight() is not None:
            return None
        self._budget.note_tool()
        name, arguments = self._take_pending_tool(io.correlation_id)
        return IN_FLIGHT_TOOL.set(InFlightTool(owner=id(self), name=name, arguments=arguments, call=io.correlation_id))

    def _tool_failure_return(self, io: InvokeEngineIo, exc: BaseException) -> EngineIoReturn:
        classified = (
            exc
            if isinstance(exc, ClassifiedFailure)
            else ClassifiedFailure(str(exc), failure_class=FailureClass.APPLICATION, code="TOOL_ERROR")
        )
        pending = self._in_flight()
        tool = pending.name if pending else "tool"
        text = self._unavailable_tool_observation(tool, classified)
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="TOOL_RESULT",
            next_step="LLM_CALL" if self.use_tool_loop else "STOP",
            text=text,
            failure_class=classified.failure_class.value,
            failure_code=classified.code,
            retry_attempts=classified.attempts,
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "LLM_CALL":
            await self.atick()
        token = self._begin_tool_call(io)
        try:
            if io.op == "TOOL_CALL" and self._tool_stack is not None:
                return await self._tool_stack.ainvoke(io)
            if self._infra_stack is not None:
                return await self._infra_stack.ainvoke(io)
            return await self._adispatch(io)
        except (ClassifiedFailure, ToolExecutionError) as exc:
            if io.op != "TOOL_CALL":
                raise
            return self._tool_failure_return(io, exc)
        finally:
            if token is not None:
                IN_FLIGHT_TOOL.reset(token)

    async def _adispatch(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "LLM_CALL":
            return await self._allm_call(io)
        if io.op == "TOOL_CALL":
            pending = self._in_flight()
            if pending is None:
                if not self._budget.allow_tool():
                    return EngineIoReturn(
                        correlation_id=io.correlation_id,
                        response_kind="ERROR",
                        next_step="STOP",
                        text="Budget exceeded: max tool calls reached.",
                    )
                self._budget.note_tool()
                tool, args = self._take_pending_tool(io.correlation_id)
            else:
                tool, args = pending.name, pending.arguments
            user = (self.ctx.last_user_text if self.ctx else "") or ""
            from mas.runtime.engine.tool_dispatch import aexecute_engine_tool

            text = await aexecute_engine_tool(
                tool,
                delegation=self.delegation,
                ctx=self.ctx,
                user=user,
                arguments=args,
                tool_provider=self.tool_provider,
                engine_contracts=self.engine_tool_contracts,
                correlation_id=io.correlation_id,
                caller_call_id=io.call_id,
                placement=self.tool_placement,
            )
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="TOOL_RESULT",
                next_step="LLM_CALL" if self.use_tool_loop else "STOP",
                text=text,
            )
        if io.op == "MEMORY_OP":
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="Memory updated.",
            )
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="ERROR",
            next_step="STOP",
            text=f"Unsupported operation: {io.op}",
        )

    def _llm_error_return(self, io: InvokeEngineIo, exc: Exception) -> EngineIoReturn:
        """The ERROR return for a provider failure, classified and logged. Sync and async share it."""
        logger.debug("LLM provider call failed", exc_info=True)
        classified = exc if isinstance(exc, ClassifiedFailure) else classify_llm_failure(exc)
        from mas.runtime.reliability.log import LOGGER as reliability_log
        from mas.runtime.reliability.log import extra as reliability_extra

        reliability_log.error(
            "LLM call exhausted (%s %s, %s attempts): %s",
            classified.failure_class.value if isinstance(classified, ClassifiedFailure) else "",
            classified.code if isinstance(classified, ClassifiedFailure) else "",
            classified.attempts if isinstance(classified, ClassifiedFailure) else 0,
            classified,
            extra=reliability_extra(
                target=self.model,
                failure_class=classified.failure_class.value if isinstance(classified, ClassifiedFailure) else "",
                failure_code=classified.code if isinstance(classified, ClassifiedFailure) else "",
                attempt=classified.attempts if isinstance(classified, ClassifiedFailure) else 0,
                outcome="llm_error",
            ),
        )
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="ERROR",
            next_step="STOP",
            text=str(classified) if isinstance(classified, ClassifiedFailure) else classify_llm_http_error(exc),
            offered_tools=list(self._offered_tool_names),
            failure_class=classified.failure_class.value if isinstance(classified, ClassifiedFailure) else "",
            failure_code=classified.code if isinstance(classified, ClassifiedFailure) else "",
            retry_attempts=classified.attempts if isinstance(classified, ClassifiedFailure) else 0,
        )

    async def _allm_call(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not self._budget.allow_llm():
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text="Budget exceeded: max LLM calls reached.",
            )
        self._budget.note_llm()
        if self.ctx is not None:
            self.ctx._assembly_correlation_id = io.correlation_id
        tool_defs = self._tool_defs()
        messages = await self._abuild_messages(tools=tool_defs)
        tools = llm_request_tools(messages, tools=tool_defs or None)
        answering_from_tools = has_tool_results(messages)
        stream = self._reasoning_for(io.correlation_id)
        token = _REASONING_STREAM.set(stream)
        try:
            message, retries = await self._acomplete_with_truncation_policy(
                messages,
                tools=tools,
                temperature=sampling_temperature(self.temperature, answering_from_tools=answering_from_tools),
            )
            self._report_final_reasoning(stream, message)
        except asyncio.CancelledError:
            raise
        except TruncatedCompletionError as exc:
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=str(exc),
                finish_reason="length",
                offered_tools=list(self._offered_tool_names),
            )
        except Exception as exc:
            return self._llm_error_return(io, exc)
        finally:
            _REASONING_STREAM.reset(token)

        usage = message.pop("usage", None) or {}
        finish_reason = message.pop("finish_reason", None) or ""
        sent_max_tokens = message.pop("_max_tokens", None)
        cache_status = message.pop("_mas_cache_status", None)
        ret = self._message_to_engine_return(
            io,
            message,
            messages,
            tool_defs,
            answering_from_tools,
            usage,
            finish_reason,
            cache_status=cache_status,
            cache_layer="provider_cache" if cache_status else "",
            cache_events=([{"layer": "provider_cache", "status": cache_status}] if cache_status else []),
            pricing=dict(self.output_limits.pricing if self.output_limits is not None else {}),
        )
        if sent_max_tokens is None and not retries:
            return ret
        return ret.model_copy(update={"max_tokens": sent_max_tokens, "truncation_retries": retries})

    async def _acomplete_with_truncation_policy(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Async counterpart to `_complete_with_truncation_policy`."""
        assert self.output_limits is not None
        limits = self.output_limits.fit_to_prompt(estimate_prompt_tokens(messages, tools))
        call_kwargs: dict[str, Any] = {"tools": tools, "temperature": temperature}
        if model is not None:
            call_kwargs["model"] = model
        if limits != self.output_limits:
            call_kwargs["limits"] = limits
        message = await self._achat_completion(messages, **call_kwargs)
        policy = limits.truncation
        retries = 0
        usage = message.get("usage")
        while message.get("finish_reason") == "length" and policy.action == "escalate" and retries < policy.retries:
            nxt = limits.escalated((message.get("usage") or {}).get("completion_tokens"))
            if nxt is None or not self._budget.allow_llm():
                break
            logger.warning(
                "LLM completion truncated (model=%s, max tokens=%s); retrying with %d",
                model or self.model,
                limits.budget,
                nxt,
            )
            self._budget.note_llm()
            retries += 1
            limits = limits.with_budget(nxt)
            message = await self._achat_completion(messages, **{**call_kwargs, "limits": limits})
            usage = sum_usage(usage, message.get("usage"))
        if usage:
            message["usage"] = usage
        if limits.budget is not None:
            message["_max_tokens"] = limits.budget
        if message.get("finish_reason") == "length" and policy.action != "ignore":
            detail = (
                f"LLM completion truncated at max tokens={limits.budget or 'server default'} "
                f"(model={model or self.model}, retries={retries}). Raise spec.models[].max_tokens "
                "or set on_truncation."
            )
            if policy.action == "error":
                raise TruncatedCompletionError(detail)
            logger.warning(detail)
        return message, retries

    def _message_to_engine_return(
        self,
        io: InvokeEngineIo,
        message: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_defs: list[dict[str, Any]],
        answering_from_tools: bool,
        usage: dict[str, Any],
        finish_reason: str,
        *,
        cache_status: str | None = None,
        cache_layer: str = "",
        cache_events: list[dict[str, str]] | None = None,
        pricing: dict[str, Any] | None = None,
    ) -> EngineIoReturn:
        _ = messages, answering_from_tools
        message = maybe_recover_textual_tool_calls(message, tool_defs)
        tool_calls = message.get("tool_calls") or []
        provider_state = message.get("provider_state")
        provider_state = dict(provider_state) if isinstance(provider_state, dict) else {}
        if tool_calls and self.use_tool_loop:
            parsed: list[tuple[str, dict[str, Any]]] = []
            for call in tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "tool")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {"raw": raw_args}
                if isinstance(args, dict):
                    args = repair_merged_arg_keys(args)
                parsed.append((name, args))
            if len(parsed) > 1:
                from mas.runtime.schema.ingress import ToolCallSpec

                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="PARALLEL_TOOL_CALLS",
                    parallel_tools=tuple(ToolCallSpec(tool_name=name, tool_arguments=args) for name, args in parsed),
                    text="",
                    usage=usage,
                    finish_reason=finish_reason,
                    provider_state=provider_state,
                    offered_tools=tool_def_names(tool_defs),
                    cache_status=cache_status,
                    cache_layer=cache_layer,
                    cache_events=cache_events or [],
                    pricing=pricing or {},
                )
            name, args = parsed[0]
            self._pending_tool = name
            self._pending_tool_args = args
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name=name,
                tool_arguments=args,
                text="",
                usage=usage,
                finish_reason=finish_reason,
                provider_state=provider_state,
                offered_tools=tool_def_names(tool_defs),
                cache_status=cache_status,
                cache_layer=cache_layer,
                cache_events=cache_events or [],
                pricing=pricing or {},
            )

        from mas.runtime.contracts.content_types import text_of

        text = text_of(message.get("content")).strip()
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            offered_tools=tool_def_names(tool_defs),
            cache_status=cache_status,
            cache_layer=cache_layer,
            cache_events=cache_events or [],
            pricing=pricing or {},
        )

    def _reasoning_for(self, correlation_id: int) -> ReasoningStream | None:
        """The reasoning interval of the LLM call ``correlation_id``, reported to observability.

        Each boundary reaches ``record_llm_thinking`` when it happens (the
        provider reports reasoning and answer deltas as they arrive). ``None``
        when nothing records it.
        """
        op = getattr(self.ctx, "observability", None) if self.ctx is not None else None
        record = getattr(op, "record_llm_thinking", None) if op is not None else None
        if not callable(record):
            return None

        def listener(boundary: str, text: str, at: float) -> None:
            record(correlation_id=correlation_id, boundary=boundary, text=text, at=at)

        return ReasoningStream(listener)

    @staticmethod
    def _report_final_reasoning(stream: ReasoningStream | None, message: dict[str, Any]) -> None:
        """Close the interval; report reasoning a provider returned only in its final message.

        A provider that reports no deltas but keeps the reasoning in the message
        (``reasoning.exclude: false``) is reported as received now: a
        zero-length interval. A message without reasoning text reports nothing.
        """
        if stream is None:
            return
        stream.close()
        if not stream.reported:
            stream.received(reasoning_text(message) + think_block_text(message.get("content")))

    async def _abuild_messages(self, *, tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if self.ctx:
            limits = self.output_limits
            return await assemble_llm_messages(
                self.ctx,
                manifest=self.manifest,
                tools=tools,
                resolved_model=self.model,
                context_window=limits.context_window if limits is not None else None,
                completion_tokens=limits.budget if limits is not None else None,
            )
        return [{"role": "user", "content": "Hello"}]

    def _tool_defs(self) -> list[dict[str, Any]]:
        """OpenAI tool schemas for this agent, and the names recorded on the call."""
        if not self.use_tool_loop:
            self._offered_tool_names = []
            return []
        tool_defs = openai_tools(
            self.manifest,
            base_dir=self.manifest_dir,
            tool_provider=self.tool_provider,
            peer_descriptions=self.delegation_peer_descriptions,
            ctx=self.ctx,
        )
        self._offered_tool_names = tool_def_names(tool_defs)
        return tool_defs

    def _unavailable_tool_observation(self, tool: str, exc: BaseException) -> str:
        available = ", ".join(self._offered_tool_names) if self._offered_tool_names else "(none)"
        return f"{exc}. Available tools: {available}."

    async def _achat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        api_key: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
        limits: OutputLimits | None = None,
    ) -> dict[str, Any]:
        """Dispatch to ``llm_provider.achat_completion``. Tests may patch this method."""
        limits = limits or self.output_limits or OutputLimits()
        provider = self.llm_provider
        if provider is None:
            raise RuntimeError("no LLM provider configured")
        on_chunk = self.ctx.on_stream_chunk if self.ctx is not None else None
        use_model = (model or self.model or "").strip() or self.model
        from mas.runtime.contracts.content_types import check_messages_modalities

        check_messages_modalities(use_model, messages)
        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "tools": tools,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": limits.max_tokens,
            "max_completion_tokens": limits.max_completion_tokens,
            "stream": self.stream,
            "on_stream_chunk": on_chunk,
            "reasoning_effort": self.reasoning.effort,
            "reasoning": self.reasoning,
            "sampling": self.sampling.to_spec_dict() or None,
            "extra_body": self.extra_body or None,
            "reasoning_stream": _REASONING_STREAM.get(),
        }
        if api_key is not None:
            kwargs["api_key"] = api_key
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        from mas.runtime.registry.llm_provider_protocol import completion_stream

        stream_iter = completion_stream(provider)
        # ainvoke is the cancellable path. Stream when the provider can, so
        # cancel lands between tokens and append_partial has a prefix to keep.
        if stream_iter is not None:
            kwargs["stream"] = True
            return await self._achat_completion_streamed(stream_iter, kwargs)
        return await provider.achat_completion(**kwargs)

    async def _achat_completion_streamed(self, stream_iter: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Yielded chunks are obs ``llm_delta``, not Mealy ticks. Cancel between awaits."""
        assembled: dict[str, Any] | None = None
        parts: list[str] = []
        try:
            reasoning = kwargs.get("reasoning_stream")
            async for chunk in stream_iter(**kwargs):
                text = ""
                if isinstance(chunk, dict) and ("role" in chunk or "tool_calls" in chunk) and "delta" not in chunk:
                    assembled = chunk
                    text = str(chunk.get("content") or "")
                elif isinstance(chunk, dict):
                    if reasoning is not None:
                        reasoning.reasoning(reasoning_text(chunk))
                    text = str(chunk.get("delta") or chunk.get("content") or "")
                    parts.append(text)
                else:
                    text = str(chunk)
                    parts.append(text)
                if text:
                    if reasoning is not None:
                        reasoning.answer()
                    self._emit_llm_delta(text)
        except asyncio.CancelledError:
            raise
        if assembled is not None:
            return assembled
        return {"role": "assistant", "content": "".join(parts), "finish_reason": "stop"}

    def _emit_llm_delta(self, text: str) -> None:
        session_id = str(self.ctx.session_id or "") if self.ctx is not None else ""
        if session_id:
            from mas.runtime.engine.inflight_llm import append_partial

            append_partial(session_id, text)
        op = getattr(self.ctx, "observability", None) if self.ctx is not None else None
        record = getattr(op, "record_session", None) if op is not None else None
        if callable(record):
            record("llm_delta", text=text)
