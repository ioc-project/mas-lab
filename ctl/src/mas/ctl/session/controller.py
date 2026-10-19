#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session controller — ctl drives runtime via Σ_in; shared by stdout and curses UI."""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from mas.ctl.session.exchange_log import (
    CliTraceExchangePlugin,
    TraceFormatOptions,
)
from mas.ctl.ui.display import ConversationDisplay
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryRegistry,
    get_working_memory_registry,
    sync_working_memory_in,
    sync_working_memory_out,
)
from mas.runtime.boundary.obs.exchange_plugin import ExchangePlugin
from mas.runtime.driver.driver import DriverTrace, ExchangeRecord
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.schema.egress import EmitClientResponse
from mas.runtime.session import BacktrackCapReached, Session, SessionStatus
from mas.runtime.boundary.control.contract import SessionBusy, SessionPaused

_logger = logging.getLogger("mas.runtime")
_RED = "\033[1;31m"
_RESET = "\033[0m"


class _ToolErrorAndListenerBridge(ExchangePlugin):
    """Second, independent ExchangePlugin subscriber alongside CliTraceExchangePlugin.

    Two behaviors that aren't display formatting, so they don't belong in
    CliTraceExchangePlugin (a generic, reusable trace renderer):
      - Always-on tool-call error surfacing: a tool_result whose text
        is a JSON error result is printed in RED to stderr and logged at ERROR
        level, regardless of --trace/--verbose, so a failing tool call (e.g.
        run_skill_script returning {"error": ...}) can never silently
        disappear from view.
      - Forwards every exchange record to an optional external listener (e.g.
        a chat-UI plugin) via SessionController.exchange_listener, if set.
    Subscribing this as its own plugin (rather than folding it into
    CliTraceExchangePlugin) keeps each subscriber single-purpose, and proves
    the additive subscribe_exchange() interface actually supports more than
    one concurrent consumer.
    """

    def __init__(self) -> None:
        self.agent_id = "n/a"
        self.exchange_listener: Any | None = None
        self.checkpoint_callback: Any | None = None

    def configure(
        self,
        *,
        agent_id: str,
        exchange_listener: Any | None,
        checkpoint_callback: Any | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.exchange_listener = exchange_listener
        self.checkpoint_callback = checkpoint_callback

    def on_exchange(self, record: ExchangeRecord) -> None:
        if callable(self.checkpoint_callback):
            try:
                self.checkpoint_callback(record)
            except Exception:
                _logger.exception("session checkpoint callback failed")
        if record.kind == "tool_result" and '"error"' in record.text:
            message = f"[{self.agent_id}] TOOL ERROR: {record.text.strip()}"
            print(f"{_RED}{message}{_RESET}", file=sys.stderr, flush=True)
            _logger.error(message)
        if callable(self.exchange_listener):
            try:
                self.exchange_listener(record)
            except Exception:
                _logger.exception("session exchange listener failed")


@dataclass
class ConversationConfig:
    single_turn: bool = False
    save_checkpoint_each_turn: bool = False
    checkpoint_label_prefix: str = "turn"


@dataclass
class TurnResult:
    trace: DriverTrace
    responses: list[EmitClientResponse] = field(default_factory=list)
    awaiting_hitl: bool = False

    @property
    def text(self) -> str:
        parts = [r.content for r in self.responses if getattr(r, "content", "")]
        return "\n".join(parts).strip()


@dataclass
class SessionController:
    """Ctl control-plane session — same backend for CLI, curses, REST/WS adapters."""

    instance: RuntimeInstance
    display: ConversationDisplay
    hitl_terminal: Any | None = None
    checkpoint_store: Any | None = None
    managed_session: Session | None = field(default=None, repr=False)
    config: ConversationConfig = field(default_factory=ConversationConfig)
    verbose: int = 0
    trace: bool = False
    trace_timestamps: bool = False
    trace_engine: bool = False
    trace_summary: bool = False
    trace_color: bool = False
    agent_id: str = "n/a"
    llm_id: str = ""
    obs_recorder: Any | None = None
    exchange_listener: Any | None = None
    exchange_hub: Any | None = field(default=None, repr=False)
    # One id for the whole MAS run — every turn this controller ever runs
    # shares it (see _run_user_turn). Empty here means "mint a fresh one";
    # explicitly pass an existing value (e.g. from an entry agent's own
    # SessionController, threaded through make_workflow_send) to make a
    # delegated agent's calls part of that SAME session instead of starting
    # a new one — session_id is global to the MAS, unlike each turn's own
    # task_id, which stays local to whichever agent runs it.
    session_id: str = ""
    # When set, this controller is running a delegated turn. The specialist's
    # final EmitClientResponse is logged as AGENT->AGENT[caller], not AGENT->USER.
    caller_agent_id: str = ""
    # Overrides session_id as the WorkingMemoryRegistry key for this
    # controller's own agent (see _working_memory_key). Empty means use
    # session_id. Set by make_workflow_send when a delegate supplies context_id.
    working_memory_key: str = ""
    working_memory_registry: WorkingMemoryRegistry | None = field(default=None, repr=False)
    _turn: int = 0
    inflight: bool = field(default=False, repr=False)
    _turn_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    _trace_turn_start: float = 0.0
    _trace_plugin: CliTraceExchangePlugin | None = field(default=None, repr=False)
    _bridge_plugin: _ToolErrorAndListenerBridge | None = field(default=None, repr=False)
    _hub_plugin: Any | None = field(default=None, repr=False)
    _checkpoint_event_counter: int = field(default=0, repr=False)

    @property
    def turn(self) -> int:
        return self._turn

    def restore_turn(self, n: int) -> None:
        """Set the completed-turn counter after checkpoint restore or backtrack."""
        if n < 0:
            raise ValueError("turn must be >= 0")
        self._turn = int(n)

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = str(uuid.uuid4())

    def _working_memory_key(self) -> str:
        return self.working_memory_key or self.session_id

    def _trace_format_options(self) -> TraceFormatOptions:
        from mas.runtime.driver.driver import engine_model_id

        engine = getattr(self.instance, "driver", None)
        engine = getattr(engine, "engine", None) if engine is not None else None
        llm_name = engine_model_id(engine) or self.llm_id
        return TraceFormatOptions(
            timestamps=self.trace_timestamps,
            engine_io=self.trace_engine,
            summary_only=self.trace_summary,
            turn_start_mono=self._trace_turn_start,
            color=self.trace_color,
            agent_name=self.agent_id,
            llm_name=llm_name,
        )

    def _setup_exchange_tracing(self) -> None:
        """Configure the CLI trace + error/listener-bridge ExchangePlugins.

        Subscribes two persistent, single-purpose plugins to the driver's
        exchange_plugins list the first time this runs, then only updates
        their config on every subsequent turn — the driver's subscriber
        list is additive (see KernelDriver.subscribe_exchange), so
        re-running this every turn no longer risks discarding any other
        plugin (e.g. an external chat-UI plugin) registered on the same
        driver:
          - CliTraceExchangePlugin: normal exchange display (AGENT/LLM/TOOL
            lines), gated by --trace or --verbose as before.
          - _ToolErrorAndListenerBridge: tool-call *errors* are always
            surfaced — unconditionally printed in RED to stderr and logged
            at ERROR level, regardless of trace/verbose settings, so a
            failing tool call (e.g. run_skill_script returning
            {"error": ...}) can never silently disappear from view. Also
            forwards every exchange record to self.exchange_listener, if set.
        """
        if self._trace_plugin is None:
            self._trace_plugin = CliTraceExchangePlugin()
            self.instance.driver.subscribe_exchange(self._trace_plugin)
        if self._bridge_plugin is None:
            self._bridge_plugin = _ToolErrorAndListenerBridge()
            self.instance.driver.subscribe_exchange(self._bridge_plugin)
        if self._hub_plugin is None and self.exchange_hub is not None:
            from mas.runtime.boundary.obs.exchange_hub import HubExchangePlugin

            self._hub_plugin = HubExchangePlugin(self.exchange_hub, self.session_id)
            self.instance.driver.subscribe_exchange(self._hub_plugin)
        self._bridge_plugin.configure(
            agent_id=self.agent_id,
            exchange_listener=self.exchange_listener,
            checkpoint_callback=self._checkpoint_after_exchange,
        )

        # Skip normal display if neither trace nor verbose logging requested
        # (the error/listener bridge stays active regardless).
        if not self.trace and self.verbose < 1:
            self._trace_plugin.configure(
                trace=False, verbose=0, agent_id=self.agent_id, fmt=self._trace_format_options()
            )
            self.instance.driver.capture_engine_io = False
            return

        self._trace_turn_start = time.perf_counter()
        self.instance.driver.capture_engine_io = self.trace_engine
        self._trace_plugin.configure(
            trace=self.trace,
            verbose=self.verbose,
            agent_id=self.agent_id,
            fmt=self._trace_format_options(),
        )


    def _handle_list_skills(self) -> TurnResult:
        """Handle /skills command — list all available skills."""
        ctx = getattr(self.instance.driver, "ctx", None)
        registry = getattr(ctx, "skill_registry", None)
        if registry is None:
            self.display.on_system(
                "Skills are not configured for this agent (no SkillCatalogPlugin attached)."
            )
        elif not registry:
            self.display.on_system("No skills registered for this agent.")
        else:
            lines = ["Available skills:"]
            for rec in registry.all():
                activated = ""
                session_state = getattr(ctx, "skill_session_state", None)
                if session_state and session_state.is_activated(rec.name):
                    activated = " [activated]"
                lines.append(f"  /skill {rec.name}{activated} — {rec.description}")
            lines.append("Use /skill <name> to activate a skill.")
            self.display.on_system("\n".join(lines))
        return TurnResult(trace=None, responses=[])

    def _handle_activate_skill(self, name: str) -> TurnResult:
        """Handle /skill <name> command — user-explicit skill activation.

        Calls activate_skill() directly (harness-side), injects the body into
        ctx.injected_context so the model sees it on the next turn, and prints
        a confirmation.  This is the agentskills.io Step 4 user-explicit path:
        the harness handles lookup and injection without an LLM turn.
        """
        if not name:
            self.display.on_system("Usage: /skill <name>")
            return TurnResult(trace=None, responses=[])

        ctx = getattr(self.instance.driver, "ctx", None)

        try:
            from mas.library.skills.plugins.sk_tools import SkillToolsPlugin
        except ImportError:
            self.display.on_system("mas-library-skills not installed.")
            return TurnResult(trace=None, responses=[])

        plugin = SkillToolsPlugin()
        result = plugin.on_execute_tool("activate_skill", {"name": name}, ctx=ctx)

        if "error" in result:
            self.display.on_system(f"Skill error: {result['error']}")
        elif result.get("already_activated"):
            self.display.on_system(result.get("notice", f"Skill '{name}' already active."))
        else:
            content = result.get("content", "")
            if content and ctx is not None:
                injected = getattr(ctx, "injected_context", None)
                if injected is not None:
                    injected.append(content)
                    # Re-capture baseline so /reset preserves the injected skill
                    capture = getattr(ctx, "capture_baseline", None)
                    if callable(capture):
                        capture()
            compat = result.get("compatibility")
            note = f" (compatibility: {compat})" if compat else ""
            self.display.on_system(f"Skill '{name}' activated{note}.")
        return TurnResult(trace=None, responses=[])

    def reset_session(self) -> bool:
        """Clear working memory and turn history; restore baseline system prompt."""
        try:
            self.instance.reset_session()
        except RuntimeError as exc:
            self.display.on_system(str(exc))
            return False
        # Drop this agent's registry snapshot too -- otherwise the *next*
        # turn's sync_working_memory_in() would restore the pre-reset
        # history right back, silently undoing the reset the user just asked
        # for (see docs/design/working-memory-compaction.md).
        (self.working_memory_registry or get_working_memory_registry()).drop(
            self._working_memory_key(), self.agent_id
        )
        self.restore_turn(0)
        self.display.on_system(
            "session reset — working memory and turn history cleared; system prompt restored"
        )
        return True

    def _finalize_turn(self, result: TurnResult, *, trace: DriverTrace | None = None) -> None:
        """Commit completed turn to ctx (chat history + tool trajectory).

        Folds working memory into committed history whenever there's
        anything to fold — a final response, tool calls that already ran
        this turn, or both — even when the turn is still paused awaiting
        HITL. Whatever already executed this turn (e.g. a delegated
        sub-agent's tool calls before an approval gate) is real conversation
        content: it must not be silently dropped the moment the *next* turn
        starts and ``note_user_input()`` clears working memory (see
        ``docs/design/working-memory-compaction.md``). Checkpointing is
        unaffected — still only for a turn that actually completed.
        """
        response_text = result.text
        ctx = getattr(self.instance.driver, "ctx", None)
        note_resp = getattr(ctx, "note_agent_response", None)
        has_working_memory = bool(
            getattr(getattr(ctx, "working_memory", None), "messages", None)
        )
        if callable(note_resp) and (response_text or has_working_memory):
            note_resp(response_text)
        sync_working_memory_out(
            self.instance,
            memory_key=self._working_memory_key(),
            agent_id=self.agent_id,
            registry=self.working_memory_registry,
        )
        if result.awaiting_hitl:
            return
        policy = self.managed_session.checkpoint_policy if self.managed_session is not None else None
        policy_checkpoint = bool(
            policy
            and (
                policy.mode == "every_turn"
                or (
                    policy.mode == "on_event"
                    and "every_n_turns" in policy.triggers
                    and policy.every_n_turns is not None
                    and self._turn % policy.every_n_turns == 0
                )
            )
        )
        if (self.config.save_checkpoint_each_turn or policy_checkpoint) and self.checkpoint_store is not None:
            label = f"{self.config.checkpoint_label_prefix}-{self._turn}"
            if self.managed_session is not None:
                self.managed_session.checkpoint(self.checkpoint_store, label=label)
            else:
                self.checkpoint_store.save(self.instance.record_checkpoint(label))

    def _checkpoint_after_exchange(self, record: ExchangeRecord) -> None:
        session = self.managed_session
        policy = session.checkpoint_policy if session is not None else None
        if policy is None or policy.mode != "on_event" or self.checkpoint_store is None:
            return
        trigger = (
            (record.kind == "llm_response" and "after_llm_call" in policy.triggers)
            or (record.kind == "tool_result" and "after_tool_call" in policy.triggers)
            or (
                record.kind == "tool_call"
                and record.destructive
                and "before_destructive_tool" in policy.triggers
            )
        )
        if trigger:
            self._checkpoint_event_counter += 1
            session.checkpoint(
                self.checkpoint_store,
                label=f"event-{self._turn:04d}-{self._checkpoint_event_counter:04d}",
            )

    def run_turn(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        auto_hitl: bool = True,
        parent_call_id: str = "",
    ) -> TurnResult:

        if text.strip().lower() == "/backtrack" or text.strip().lower().startswith("/backtrack "):
            return self._handle_backtrack(text)

        inject_now = text.strip().lower().startswith("/steer ")
        with self._turn_lock:
            if self.inflight:
                raise SessionBusy(self.session_id or "session", method="run_turn")
            self.inflight = True
        try:
            if inject_now:
                return self._handle_steer(text, turn_id=turn_id, auto_hitl=auto_hitl)

            stripped = text.strip()
            if stripped.lower() == "/skills":
                return self._handle_list_skills()
            if stripped.lower() == "/skill" or stripped.lower().startswith("/skill "):
                skill_name = stripped[len("/skill"):].strip()
                return self._handle_activate_skill(skill_name)

            self._reject_if_paused()
            text = self._take_queued_or_direct(text)
            if text.strip().lower().startswith("/steer "):
                return self._handle_steer(text, turn_id=turn_id, auto_hitl=auto_hitl)
            return self._run_user_turn(
                text, turn_id=turn_id, auto_hitl=auto_hitl, parent_call_id=parent_call_id
            )
        finally:
            with self._turn_lock:
                self.inflight = False

    def _reject_if_paused(self) -> None:
        session = self.managed_session
        if session is not None and session.status is SessionStatus.PAUSED:
            raise SessionPaused(session.session_id, reason=session.pause_reason)

    def _take_queued_or_direct(self, text: str) -> str:
        queue = getattr(self, "turn_queue", None)
        session_id = getattr(self, "session_id", "") or ""
        if queue is None or not session_id:
            return text
        if text:
            queue.enqueue(session_id, text, source="run_turn")
        item = queue.pop(session_id)
        if item is None:
            return text
        return item.text

    def _handle_steer(self, text: str, *, turn_id: str | None, auto_hitl: bool) -> TurnResult:
        from mas.runtime.schema.ingress import OperatorSteerReceived

        steer_text = text.strip()[7:].strip()
        self._turn += 1
        tid = turn_id or f"steer{self._turn}"
        on_system = getattr(self.display, "on_system", None)
        if callable(on_system):
            on_system(f"operator steer: {steer_text}")
        self._setup_exchange_tracing()
        sync_working_memory_in(
            self.instance,
            memory_key=self._working_memory_key(),
            agent_id=self.agent_id,
            registry=self.working_memory_registry,
        )
        # New exclusive cycle when idle. While generating, ControlContract.steer
        # preempts streamed tokens into this same session instead of calling here.
        trace = self.instance.feed(
            OperatorSteerReceived(steer_id=tid, context_text=steer_text)
        )
        if auto_hitl:
            trace = self._drain_hitl(trace)
        self._present_trace(trace)
        return TurnResult(trace=trace, responses=list(trace.client_responses))

    async def arun_turn(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        auto_hitl: bool = True,
        parent_call_id: str = "",
    ) -> TurnResult:
        """Async twin of :meth:`run_turn` — awaits ``instance.arun_user_text``."""

        if text.strip().lower() == "/backtrack" or text.strip().lower().startswith("/backtrack "):
            return self._handle_backtrack(text)

        if text.strip().lower().startswith("/steer "):
            return self._handle_steer(text, turn_id=turn_id, auto_hitl=auto_hitl)

        stripped = text.strip()
        if stripped.lower() == "/skills":
            return self._handle_list_skills()
        if stripped.lower() == "/skill" or stripped.lower().startswith("/skill "):
            skill_name = stripped[len("/skill"):].strip()
            return self._handle_activate_skill(skill_name)

        self._reject_if_paused()
        text = self._take_queued_or_direct(text)
        return await self._arun_user_turn(
            text, turn_id=turn_id, auto_hitl=auto_hitl, parent_call_id=parent_call_id
        )

    def _handle_backtrack(self, text: str) -> TurnResult:
        """Restore retained session state and optionally inject a steering note."""
        if self.managed_session is None or self.checkpoint_store is None:
            self.display.on_system("backtrack unavailable: no managed checkpoint session")
            return TurnResult(trace=None, responses=[])
        parts = text.strip().split(maxsplit=2)
        steps = 1
        steering_text = ""
        if len(parts) > 1:
            try:
                steps = int(parts[1])
                steering_text = parts[2] if len(parts) > 2 else ""
            except ValueError:
                steering_text = " ".join(parts[1:])
        try:
            path = self.managed_session.backtrack(
                self.checkpoint_store,
                steps=steps,
                steering_text=steering_text,
            )
        except (OSError, ValueError) as exc:
            self.display.on_system(f"backtrack failed: {exc}")
            return TurnResult(trace=None, responses=[])
        self.display.on_system(f"backtracked to checkpoint: {path}")
        return TurnResult(trace=None, responses=[])

    def _run_user_turn(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        auto_hitl: bool = True,
        parent_call_id: str = "",
    ) -> TurnResult:
        from mas.ctl.manifest.mas_agent_merge import reset_engine_delegation

        reset_engine_delegation(getattr(self.instance.driver, "engine", None))
        sync_working_memory_in(
            self.instance,
            memory_key=self._working_memory_key(),
            agent_id=self.agent_id,
            registry=self.working_memory_registry,
        )
        self._turn += 1
        tid = turn_id or f"u{self._turn}"
        if hasattr(self.instance, "driver") and hasattr(self.instance.driver, "caller_agent_id"):
            self.instance.driver.caller_agent_id = self.caller_agent_id
        if self.display is not None:
            self.display.on_user(text, turn_id=tid)
        on_working = getattr(self.display, "on_working", None)
        if callable(on_working):
            on_working()
        self._setup_exchange_tracing()
        trace = self.instance.run_user_text(
            text, turn_id=tid, parent_call_id=parent_call_id, session_id=self.session_id
        )
        end_working = getattr(self.display, "end_working", None)
        if callable(end_working):
            end_working()
        while True:
            if auto_hitl:
                trace = self._drain_hitl(trace)
            if not auto_hitl:
                break
            if not self._session_awaiting_operator():
                break
            if self.hitl_terminal is None or not trace.hitl_requests:
                break
        self._present_trace(trace)
        result = TurnResult(
            trace=trace,
            responses=list(trace.client_responses),
            awaiting_hitl=trace.awaiting_hitl,
        )
        if self._handle_automatic_backtrack(trace):
            return result
        self._finalize_turn(result, trace=trace)
        return result

    async def _arun_user_turn(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        auto_hitl: bool = True,
        parent_call_id: str = "",
    ) -> TurnResult:
        from mas.ctl.manifest.mas_agent_merge import reset_engine_delegation

        reset_engine_delegation(getattr(self.instance.driver, "engine", None))
        sync_working_memory_in(
            self.instance,
            memory_key=self._working_memory_key(),
            agent_id=self.agent_id,
            registry=self.working_memory_registry,
        )
        self._turn += 1
        tid = turn_id or f"u{self._turn}"
        if hasattr(self.instance, "driver") and hasattr(self.instance.driver, "caller_agent_id"):
            self.instance.driver.caller_agent_id = self.caller_agent_id
        if self.display is not None:
            self.display.on_user(text, turn_id=tid)
        on_working = getattr(self.display, "on_working", None)
        if callable(on_working):
            on_working()
        self._setup_exchange_tracing()
        trace = await self.instance.arun_user_text(
            text, turn_id=tid, parent_call_id=parent_call_id, session_id=self.session_id
        )
        end_working = getattr(self.display, "end_working", None)
        if callable(end_working):
            end_working()
        while True:
            if auto_hitl:
                trace = self._drain_hitl(trace)
            if not auto_hitl:
                break
            if not self._session_awaiting_operator():
                break
            if self.hitl_terminal is None or not trace.hitl_requests:
                break
        self._present_trace(trace)
        result = TurnResult(
            trace=trace,
            responses=list(trace.client_responses),
            awaiting_hitl=trace.awaiting_hitl,
        )
        if self._handle_automatic_backtrack(trace):
            return result
        self._finalize_turn(result, trace=trace)
        return result

    def _handle_automatic_backtrack(self, trace: DriverTrace) -> bool:
        """Restore on the explicit kernel signal; patterns never own rollback."""
        session = self.managed_session
        if session is None or self.checkpoint_store is None:
            return False
        error = next(
            (
                item
                for item in trace.boundary_errors
                if getattr(item, "code", "") == "INGRESS_BACKTRACK"
            ),
            None,
        )
        if error is None:
            return False
        error_text = getattr(error, "message", "")
        steering_text = (
            f"A previous attempt failed with: {error_text}. Avoid repeating that approach."
        )
        try:
            session.backtrack(
                self.checkpoint_store,
                steps=session.backtrack_policy.get("steps", 1),
                steering_text=steering_text,
                automatic=True,
            )
        except BacktrackCapReached as exc:
            if exc.action == "stop_backtracking":
                return False
            if exc.action == "terminate":
                self.instance.abort(reason=str(exc))
                session.status = SessionStatus.TERMINATED
            else:
                self.instance.pause(reason=str(exc))
                session.status = SessionStatus.PAUSED
            self.display.on_system(str(exc))
        except (OSError, ValueError) as exc:
            self.display.on_system(f"automatic backtrack failed: {exc}")
        return True

    def submit_hitl(self, resolve, *, auto_hitl: bool = True) -> TurnResult:
        """Feed HITL_RESOLVE after UI collects operator choice."""
        self._setup_exchange_tracing()
        trace = self.instance.feed(resolve)
        if auto_hitl:
            trace = self._drain_hitl(trace)
        self._present_trace(trace)
        result = TurnResult(
            trace=trace,
            responses=list(trace.client_responses),
            awaiting_hitl=trace.awaiting_hitl,
        )
        self._finalize_turn(result, trace=trace)
        return result

    def _drain_hitl(self, trace: DriverTrace) -> DriverTrace:
        while trace.awaiting_hitl and self.hitl_terminal is not None:
            if not trace.hitl_requests:
                break
            request = trace.hitl_requests[-1]
            self.display.on_hitl_request(request)
            resolve = self.hitl_terminal.resolve(request)
            trace = self.instance.feed(resolve)
        return trace

    def _present_trace(self, trace: DriverTrace) -> None:
        # HITL already drained in _drain_hitl; do not re-print pending requests.
        if not self.trace:
            for resp in trace.client_responses:
                self._present_client_response(resp)
        for err in trace.boundary_errors:
            self._present_boundary_error(err)

    def _present_client_response(self, resp: EmitClientResponse) -> None:
        if resp.finish_reason == "error":
            self.display.on_turn_error(resp.content)
        elif resp.content.strip():
            self.display.on_agent(resp.content)

    def _present_boundary_error(self, err: object) -> None:
        code = getattr(err, "code", str(err))
        message = getattr(err, "message", "") or ""
        if message.strip():
            self.display.on_turn_error(message.strip(), detail=str(code))
        else:
            self.display.on_turn_error(str(code))

    def _session_awaiting_operator(self) -> bool:
        from mas.runtime.machines.gov import gov_is_hitl_pending

        return gov_is_hitl_pending(self.instance.kernel.q)


def close_observability(controller: SessionController) -> None:
    if controller.obs_recorder is not None:
        controller.obs_recorder.close()
    elif hasattr(controller.instance, "obs_plugin_set") and controller.instance.obs_plugin_set:
        controller.instance.obs_plugin_set.close()


def run_session_loop(
    controller: SessionController,
    *,
    interactive: bool,
    scripted: list[str],
) -> int:
    """Single entry for ctl stdout executors. Returns process exit code (0 = ok)."""
    from mas.ctl.ui.turn_result import turn_failed

    if interactive:
        from mas.ctl.session.operator_console import OperatorConsole

        OperatorConsole().run(
            controller,
            initial=scripted[0] if scripted else None,
        )
        return 0
    exit_code = 0
    for text in scripted:
        if turn_failed(controller.run_turn(text)):
            exit_code = 1
    return exit_code
