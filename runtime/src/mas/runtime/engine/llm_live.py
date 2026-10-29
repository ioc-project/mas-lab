#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live LLM engine — EngineContract over an LLMProvider plugin."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from mas.runtime.boundary.context.assemble import (
    assemble_llm_messages,
    has_tool_results,
    llm_request_tools,
)
from mas.runtime.boundary.gov.budget import BudgetTracker, budget_from_manifest
from mas.runtime.engine.exchange_preview import ExchangeSnapshot, format_exchange_snapshot
from mas.runtime.engine.llm_output_limits import (
    OutputLimits,
    estimate_prompt_tokens,
    resolve_output_limits,
    sum_usage,
)
from mas.runtime.engine.llm_reasoning import (
    ReasoningSettings,
    coerce_reasoning_settings,
    reasoning_settings_from_manifest,
)
from mas.runtime.engine.llm_request import extra_from_manifest, sampling_settings_from_manifest
from mas.runtime.engine.textual_tool_calls import maybe_recover_textual_tool_calls, repair_merged_arg_keys
from mas.runtime.engine.tool_dispatch import ToolExecutionError, execute_engine_tool
from mas.runtime.engine.tools import openai_tools
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import classify_llm_failure, classify_llm_http_error
from mas.runtime.reliability.policy import ReliabilitySettings
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

if TYPE_CHECKING:
    from mas.runtime.driver.mocks import AutoCtxAssembler

logger = logging.getLogger(__name__)


class TruncatedCompletionError(RuntimeError):
    """Raised when ``on_truncation: error`` and the completion hit its token budget."""


@dataclass
class _InfraDispatchPort:
    """Innermost LLM/tool body for infra steps. Listed members only; not a public engine."""

    owns_call_identity: ClassVar[bool] = True
    engine: LiveLlmEngine

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.engine._dispatch(io)

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return await self.engine._adispatch(io)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return self.engine.exchange_preview(op, correlation_id=correlation_id)

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
class LiveLlmEngine:
    """EngineContract implementation — delegates chat completions to LLMProvider plugins."""

    ctx: AutoCtxAssembler | None = None
    manifest: dict | None = None
    api_base: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
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
    parallel_tool_calls: bool = True
    llm_proxy: dict[str, Any] | None = None
    llm_proxies: dict[str, dict[str, Any]] | None = None
    http_timeout: float | None = None
    manifest_dir: Path | None = None
    delegation: Any | None = None
    engine_tool_contracts: tuple[Any, ...] = ()
    delegation_peer_descriptions: dict[str, str] | None = None
    tool_provider: Any | None = None
    llm_provider: Any | None = None
    reliability: ReliabilitySettings | None = None
    _pending_tool: str = field(default="", init=False)
    _pending_tool_args: dict[str, Any] = field(default_factory=dict, init=False)
    _pending_tools_by_cid: dict[int, tuple[str, dict[str, Any]]] = field(default_factory=dict, init=False)
    _offered_tool_names: list[str] = field(default_factory=list, init=False)
    _budget: BudgetTracker = field(default_factory=BudgetTracker, init=False)
    _model_access: Any | None = field(default=None, init=False)
    _infra_stack: Any = field(default=None, init=False, repr=False)

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
        if self.reliability is None:
            self.reliability = ReliabilitySettings.from_spec(spec, llm_proxy=self.llm_proxy)
        self._circuit = self.reliability.circuit_breaker
        if self.llm_provider is None:
            self.llm_provider = self._default_llm_provider()
        self.llm_provider = self._wrap_cache(self.llm_provider)
        # Tests that still inspect _model_access see the routed provider.
        self._model_access = self.llm_provider

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

        steps = [dict(step) for step in (pipeline or []) if isinstance(step, dict)]
        common, per_leaf = split_pipeline_layers(steps)
        if per_leaf:
            names = [middleware_id(step) or "?" for step in per_leaf]
            raise ValueError(
                "install_infra_pipeline only accepts per: all steps; "
                f"got destination {names}. Wrap those on each leaf."
            )
        if not common:
            self._infra_stack = None
            return
        self._infra_stack = wrap_call_stack(_InfraDispatchPort(self), common)

    def _default_llm_provider(self) -> Any:
        from mas.runtime.registry.llm_provider_registry import llm_provider_from_infra

        proxy = dict(self.llm_proxy or {})
        proxy.setdefault("api_base", self.api_base)
        proxy.setdefault("api_key_env", self.api_key_env)
        if self.http_timeout is not None:
            proxy.setdefault("timeout", self.http_timeout)
        if self.reliability is not None:
            proxy["retry"] = self.reliability.llm_retry.to_mapping()
            proxy["circuit_breaker"] = self.reliability.circuit_breaker
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

    def summarize_messages(self, messages: list[dict[str, Any]], *, model: str | None = None) -> str:
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
        message, _retries = self._complete_with_truncation_policy(
            messages, tools=None, temperature=0.0, model=use_model
        )
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return str(content or "").strip()

    def exchange_snapshot(self, op: str, *, correlation_id: int = 0) -> ExchangeSnapshot:
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
            messages = self._build_messages(tools=tool_defs)
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

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        """Pretty-print of exchange_snapshot (cache keys, tests). Not interchange."""
        return format_exchange_snapshot(self.exchange_snapshot(op, correlation_id=correlation_id))

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if self._infra_stack is not None:
            return self._infra_stack.invoke(io)
        return self._dispatch(io)

    def _dispatch(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "LLM_CALL":
            return self._llm_call(io)
        if io.op == "TOOL_CALL":
            if not self._budget.allow_tool():
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="ERROR",
                    next_step="STOP",
                    text="Budget exceeded: max tool calls reached.",
                )
            self._budget.note_tool()
            user = (self.ctx.last_user_text if self.ctx else "") or ""
            by_cid = self._pending_tools_by_cid.pop(io.correlation_id, None)
            if by_cid is not None:
                tool, args = by_cid
            else:
                tool = self._pending_tool or "tool"
                args = dict(self._pending_tool_args)
                self._pending_tool = ""
                self._pending_tool_args = {}
            try:
                text = execute_engine_tool(
                    tool,
                    delegation=self.delegation,
                    ctx=self.ctx,
                    user=user,
                    arguments=args,
                    tool_provider=self.tool_provider,
                    engine_contracts=self.engine_tool_contracts,
                    correlation_id=io.correlation_id,
                    caller_call_id=io.call_id,
                    retry_policy=None if self.reliability is None else self.reliability.tool_retry,
                    circuit_breaker=getattr(self, "_circuit", None),
                    agent_spec=((self.manifest or {}).get("spec") if isinstance(self.manifest, dict) else None),
                )
            except (ClassifiedFailure, ToolExecutionError) as exc:
                classified = (
                    exc
                    if isinstance(exc, ClassifiedFailure)
                    else ClassifiedFailure(str(exc), failure_class=FailureClass.APPLICATION, code="TOOL_ERROR")
                )
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

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if self._infra_stack is not None:
            return await self._infra_stack.ainvoke(io)
        return await self._adispatch(io)

    async def _adispatch(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "LLM_CALL":
            return await self._allm_call(io)
        if io.op == "TOOL_CALL":
            if not self._budget.allow_tool():
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="ERROR",
                    next_step="STOP",
                    text="Budget exceeded: max tool calls reached.",
                )
            self._budget.note_tool()
            user = (self.ctx.last_user_text if self.ctx else "") or ""
            by_cid = self._pending_tools_by_cid.pop(io.correlation_id, None)
            if by_cid is not None:
                tool, args = by_cid
            else:
                tool = self._pending_tool or "tool"
                args = dict(self._pending_tool_args)
                self._pending_tool = ""
                self._pending_tool_args = {}
            try:
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
                    retry_policy=None if self.reliability is None else self.reliability.tool_retry,
                    circuit_breaker=getattr(self, "_circuit", None),
                    agent_spec=((self.manifest or {}).get("spec") if isinstance(self.manifest, dict) else None),
                )
            except (ClassifiedFailure, ToolExecutionError) as exc:
                classified = (
                    exc
                    if isinstance(exc, ClassifiedFailure)
                    else ClassifiedFailure(str(exc), failure_class=FailureClass.APPLICATION, code="TOOL_ERROR")
                )
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

    def _llm_call(self, io: InvokeEngineIo) -> EngineIoReturn:
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
        messages = self._build_messages(tools=tool_defs)
        tools = llm_request_tools(messages, tools=tool_defs or None)
        answering_from_tools = has_tool_results(messages)
        try:
            message, retries = self._complete_with_truncation_policy(
                messages,
                tools=tools,
                temperature=0.0 if answering_from_tools else self.temperature,
            )
        except TruncatedCompletionError as exc:
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=str(exc),
                finish_reason="length",
                offered_tools=list(self._offered_tool_names),
                model=self.model,
            )
        except Exception as exc:
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

        usage = message.pop("usage", None) or {}
        finish_reason = message.pop("finish_reason", None) or ""
        max_tokens = message.pop("_max_tokens", None)
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
        if max_tokens is None and not retries:
            return ret
        return ret.model_copy(update={"max_tokens": max_tokens, "truncation_retries": retries})

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
        messages = self._build_messages(tools=tool_defs)
        tools = llm_request_tools(messages, tools=tool_defs or None)
        answering_from_tools = has_tool_results(messages)
        try:
            message, retries = await self._acomplete_with_truncation_policy(
                messages,
                tools=tools,
                temperature=0.0 if answering_from_tools else self.temperature,
            )
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
                model=self.model,
            )
        except Exception as exc:
            logger.debug("LLM provider call failed", exc_info=True)
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="ERROR",
                next_step="STOP",
                text=classify_llm_http_error(exc),
                offered_tools=list(self._offered_tool_names),
            )

        usage = message.pop("usage", None) or {}
        finish_reason = message.pop("finish_reason", None) or ""
        max_tokens = message.pop("_max_tokens", None)
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
        if max_tokens is None and not retries:
            return ret
        return ret.model_copy(update={"max_tokens": max_tokens, "truncation_retries": retries})

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

    def _complete_with_truncation_policy(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Run one completion and apply ``on_truncation``. Returns ``(message, retries)``."""
        assert self.output_limits is not None
        limits = self.output_limits.fit_to_prompt(estimate_prompt_tokens(messages, tools))
        call_kwargs: dict[str, Any] = {"tools": tools, "temperature": temperature}
        if model is not None:
            call_kwargs["model"] = model
        if limits != self.output_limits:
            call_kwargs["limits"] = limits
        message = self._chat_completion(messages, **call_kwargs)
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
            message = self._chat_completion(messages, **{**call_kwargs, "limits": limits})
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
            if len(parsed) > 1 and self.parallel_tool_calls:
                from mas.runtime.schema.ingress import ToolCallSpec

                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="PARALLEL_TOOL_CALLS",
                    parallel_tools=tuple(ToolCallSpec(tool_name=name, tool_arguments=args) for name, args in parsed),
                    text="",
                    usage=usage,
                    finish_reason=finish_reason,
                    offered_tools=self._names_from_tool_defs(tool_defs),
                    model=self.model,
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
                offered_tools=self._names_from_tool_defs(tool_defs),
                model=self.model,
                cache_status=cache_status,
                cache_layer=cache_layer,
                cache_events=cache_events or [],
                pricing=pricing or {},
            )

        text = str(message.get("content") or "").strip()
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            offered_tools=self._names_from_tool_defs(tool_defs),
            model=self.model,
            cache_status=cache_status,
            cache_layer=cache_layer,
            cache_events=cache_events or [],
            pricing=pricing or {},
        )

    def _build_messages(self, *, tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if self.ctx:
            limits = self.output_limits
            return assemble_llm_messages(
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
        self._offered_tool_names = self._names_from_tool_defs(tool_defs)
        return tool_defs

    @staticmethod
    def _names_from_tool_defs(tool_defs: list[dict[str, Any]] | None) -> list[str]:
        names: list[str] = []
        for tool in tool_defs or []:
            fn = tool.get("function") if isinstance(tool, dict) else None
            name = str((fn or {}).get("name") or "") if isinstance(fn, dict) else ""
            if name:
                names.append(name)
        return names

    def _unavailable_tool_observation(self, tool: str, exc: BaseException) -> str:
        available = ", ".join(self._offered_tool_names) if self._offered_tool_names else "(none)"
        return f"{exc}. Available tools: {available}."

    def _chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        api_key: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
        limits: OutputLimits | None = None,
    ) -> dict[str, Any]:
        """Dispatch to ``llm_provider``. Tests may patch this method."""
        limits = limits or self.output_limits or OutputLimits()
        provider = self.llm_provider
        if provider is None:
            raise RuntimeError("no LLM provider configured")
        on_chunk = getattr(self.ctx, "on_stream_chunk", None) if self.ctx is not None else None
        use_model = (model or self.model or "").strip() or self.model
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
        }
        if api_key is not None:
            kwargs["api_key"] = api_key
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if not hasattr(provider, "chat_completion"):
            raise RuntimeError(f"LLM provider {type(provider).__name__} has no chat_completion")
        return provider.chat_completion(**kwargs)

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
        on_chunk = getattr(self.ctx, "on_stream_chunk", None) if self.ctx is not None else None
        use_model = (model or self.model or "").strip() or self.model
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
        }
        if api_key is not None:
            kwargs["api_key"] = api_key
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if not hasattr(provider, "achat_completion"):
            raise RuntimeError(
                f"LLM provider {type(provider).__name__} has no achat_completion "
                "(async twin of chat_completion is required; worker-thread fallback is not supported)"
            )
        stream_iter = getattr(provider, "achat_completion_stream", None)
        # ainvoke is the cancellable path. Stream when the provider can, so
        # cancel lands between tokens and append_partial has a prefix to keep.
        if callable(stream_iter):
            kwargs["stream"] = True
            return await self._achat_completion_streamed(stream_iter, kwargs)
        return await provider.achat_completion(**kwargs)

    async def _achat_completion_streamed(self, stream_iter: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Yielded chunks are obs ``llm_delta``, not Mealy ticks. Cancel between awaits."""
        assembled: dict[str, Any] | None = None
        parts: list[str] = []
        try:
            async for chunk in stream_iter(**kwargs):
                text = ""
                if isinstance(chunk, dict) and ("role" in chunk or "tool_calls" in chunk) and "delta" not in chunk:
                    assembled = chunk
                    text = str(chunk.get("content") or "")
                elif isinstance(chunk, dict):
                    text = str(chunk.get("delta") or chunk.get("content") or "")
                    parts.append(text)
                else:
                    text = str(chunk)
                    parts.append(text)
                if text:
                    self._emit_llm_delta(text)
        except asyncio.CancelledError:
            raise
        if assembled is not None:
            return assembled
        return {"role": "assistant", "content": "".join(parts), "finish_reason": "stop"}

    def _emit_llm_delta(self, text: str) -> None:
        session_id = ""
        if self.ctx is not None:
            session_id = str(getattr(self.ctx, "session_id", "") or "")
        if session_id:
            from mas.runtime.engine.inflight_llm import append_partial

            append_partial(session_id, text)
        op = getattr(self.ctx, "observability", None) if self.ctx is not None else None
        record = getattr(op, "record_session", None) if op is not None else None
        if callable(record):
            record("llm_delta", text=text)
