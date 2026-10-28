#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""RuntimeInstance — embeddable control-plane surface for ctl and integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryConfig
from mas.runtime.boundary.coordination.chokepoint import ChokepointCoordinator
from mas.runtime.boundary.obs.operator import ObservabilityOperator
from mas.runtime.contracts.tool_semantics import existing_attr
from mas.runtime.driver.driver import DriverTrace, KernelDriver
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.schema.ingress import (
    IngressSymbol,
    LifecycleAbort,
    LifecyclePause,
    LifecycleResume,
    UserInputReceived,
)

if TYPE_CHECKING:
    from mas.runtime.boundary.obs.binding import ObservabilityBinding
    from mas.runtime.boundary.obs.plugins import ObsPluginSet


@dataclass
class RuntimeInstance:
    """Wraps kernel + driver; exposes pause/resume/abort and user feed."""

    kernel: RuntimeKernel
    driver: KernelDriver
    _checkpoints: list[dict] = field(default_factory=list)
    obs_plugin_set: ObsPluginSet | None = field(default=None, repr=False)
    # Mirrors spec.working_memory on the agent manifest (default:
    # persistent=True). Set by instantiate_runtime() from the agent
    # manifest; only consulted on the delegation path (make_workflow_send) —
    # an entry agent driven directly by the user already keeps its own
    # history for free across turns. See WorkingMemoryConfig.
    working_memory: WorkingMemoryConfig = field(default_factory=WorkingMemoryConfig)
    # Counts executions per ``<agent>-<turn>`` so reused turn ids stay unique.
    _exec_id_uses: dict[str, int] = field(default_factory=dict, repr=False)

    def _execution_id(self, agent_id: str, turn_id: str) -> str:
        """``<agent>-<turn>-exec``, with ``-<n>`` after the first use of that pair."""
        base = f"{agent_id}-{turn_id}" if agent_id else str(turn_id)
        n = self._exec_id_uses.get(base, 0) + 1
        self._exec_id_uses[base] = n
        return f"{base}-exec" if n == 1 else f"{base}-{n}-exec"

    def _begin_user_turn(self, text: str, turn_id: str, parent_call_id: str) -> str | None:
        op = self.driver.observability
        if op is None or self.obs_plugin_set is None:
            return None
        exec_id = self._execution_id(op._agent_id, turn_id)
        op.push_call_frame(exec_id)
        rec: dict[str, Any] = {"text": text, "call_id": exec_id, "turn_id": turn_id}
        if parent_call_id:
            rec["parent_call_id"] = parent_call_id
        op.record_session("user_input", **rec)
        return exec_id

    def _finish_user_turn(self, exec_id: str | None, trace: DriverTrace) -> None:
        op = self.driver.observability
        if op is None or exec_id is None:
            return
        response_text = "\n".join(
            r.content for r in trace.client_responses if getattr(r, "content", "")
        ).strip()
        if response_text:
            op.record_session("agent_response", text=response_text, finish_reason="stop")
        op.pop_call_frame(exec_id)

    def _user_input_event(
        self,
        text: str,
        turn_id: str,
        session_id: str,
        upstream_correlation_id: int | None,
        *,
        source: str = "",
        sender: str = "",
        intent: str = "",
    ) -> UserInputReceived:
        kwargs: dict[str, Any] = {
            "user_turn_id": turn_id,
            "text": text,
            "upstream_correlation_id": upstream_correlation_id,
            "source": source,
            "sender": sender,
            "intent": intent,
        }
        if session_id:
            kwargs["session_id"] = session_id
        return UserInputReceived(**kwargs)

    @classmethod
    def from_parts(
        cls,
        *,
        config: KernelConfig | None = None,
        hitl: Any | None = None,
        engine: SimulatedEngine | None = None,
        ctx: Any | None = None,
        enable_observability: bool = True,
        enable_coordination: bool = True,
        obs_binding: ObservabilityBinding | None = None,
        obs_base_dir: Path | None = None,
        obs_agent_id: str | None = None,
    ) -> RuntimeInstance:
        resolved_config = config or KernelConfig()
        kernel = RuntimeKernel(config=resolved_config)
        op = ObservabilityOperator() if enable_observability else None
        driver = KernelDriver(
            kernel=kernel,
            hitl=hitl,
            engine=engine or SimulatedEngine(),
            ctx=ctx or AutoCtxAssembler(),
            observability=op,
            coordination=ChokepointCoordinator() if enable_coordination else None,
            max_auto_steps=resolved_config.max_auto_steps,
        )
        instance = cls(kernel=kernel, driver=driver)

        if obs_binding is not None and op is not None:
            from mas.runtime.boundary.obs.plugins import ObsPluginSet, build_observability_plugins

            plugins = build_observability_plugins(
                obs_binding,
                base_dir=obs_base_dir or Path("."),
                agent_id=obs_agent_id,
            )
            plugin_set = ObsPluginSet(plugins=plugins)
            plugin_set.subscribe_to(
                op,
                agent_id=obs_agent_id or "",
            )
            instance.obs_plugin_set = plugin_set

        return instance

    @classmethod
    def from_spec(
        cls,
        spec: dict,
        *,
        base_dir: Any | None = None,
        agent_id: str = "",
        mas_id: str = "",
        obs_binding_override: ObservabilityBinding | None = None,
        hitl: Any | None = None,
        engine: Any | None = None,
        ctx: Any | None = None,
        enable_coordination: bool = True,
        enable_governance: bool = True,
        enable_observability: bool = True,
        runtime_engine: dict[str, Any] | None = None,
    ) -> RuntimeInstance:
        """Build a RuntimeInstance from a raw agent spec dict.

        This is the production entry point for spec-driven instantiation.
        ctl may pass obs_binding_override when CLI flags supersede spec defaults.

        ``spec`` is the inner ``spec:`` block of an agent manifest, e.g.
        ``manifest["spec"]``.
        """
        from dataclasses import replace as _replace
        from pathlib import Path as _Path

        from mas.runtime.spec.parser import parse_agent_spec

        resolved_base_dir = (_Path(base_dir) if isinstance(base_dir, str) else base_dir) or _Path(".")
        kernel_config, spec_obs_binding = parse_agent_spec(
            spec,
            runtime_engine=runtime_engine,
            manifest_dir=resolved_base_dir,
        )
        obs_binding = obs_binding_override if obs_binding_override is not None else spec_obs_binding
        if obs_binding is not None and mas_id:
            obs_binding = obs_binding.with_mas_id(mas_id)

        if not enable_governance:
            kernel_config = _replace(kernel_config, enable_governance=False)

        return cls.from_parts(
            config=kernel_config,
            hitl=hitl,
            engine=engine,
            ctx=ctx,
            obs_binding=obs_binding,
            obs_base_dir=resolved_base_dir,
            obs_agent_id=agent_id,
            enable_coordination=enable_coordination,
            enable_observability=enable_observability,
        )

    def pause(self, *, reason: str = "") -> DriverTrace:
        return self.driver.feed(LifecyclePause(reason=reason))

    def resume(self) -> DriverTrace:
        return self.driver.feed(LifecycleResume())

    def abort(self, *, reason: str = "") -> DriverTrace:
        return self.driver.feed(LifecycleAbort(reason=reason))

    def feed(self, event: IngressSymbol) -> DriverTrace:
        return self.driver.feed(event)

    def run_user_text(
        self,
        text: str,
        *,
        turn_id: str = "u1",
        parent_call_id: str = "",
        session_id: str = "",
        upstream_correlation_id: int | None = None,
        source: str = "",
        sender: str = "",
        intent: str = "",
    ) -> DriverTrace:
        """``session_id`` empty means "no one has one yet" — a genuinely new
        MAS session — and UserInputReceived mints one itself (see its
        ``session_id`` field's default_factory). Pass an existing value
        (e.g. from ``SessionController.session_id``) to continue a session
        instead of starting a new one; that's the caller's job, not this
        method's — it never mints one, only forwards or omits.
        """
        exec_id = self._begin_user_turn(text, turn_id, parent_call_id)
        trace = self.feed(
            self._user_input_event(
                text,
                turn_id,
                session_id,
                upstream_correlation_id,
                source=source,
                sender=sender,
                intent=intent,
            )
        )
        self._finish_user_turn(exec_id, trace)
        return trace

    async def afeed(self, event: IngressSymbol) -> DriverTrace:
        return await self.driver.afeed(event)

    async def arun_user_text(
        self,
        text: str,
        *,
        turn_id: str = "u1",
        parent_call_id: str = "",
        session_id: str = "",
        upstream_correlation_id: int | None = None,
        source: str = "",
        sender: str = "",
        intent: str = "",
    ) -> DriverTrace:
        """Async twin of :meth:`run_user_text` — awaits ``driver.afeed``."""
        exec_id = self._begin_user_turn(text, turn_id, parent_call_id)
        trace = await self.afeed(
            self._user_input_event(
                text,
                turn_id,
                session_id,
                upstream_correlation_id,
                source=source,
                sender=sender,
                intent=intent,
            )
        )
        self._finish_user_turn(exec_id, trace)
        return trace

    def capture_session_baseline(self) -> None:
        """Record idle kernel state at session start (for /reset)."""
        self._session_baseline = self.snapshot()

    def reset_session(self) -> None:
        """Reset conversation context and kernel to session-start idle state."""
        from mas.runtime.machines.gov import gov_is_hitl_pending

        if gov_is_hitl_pending(self.kernel.q):
            raise RuntimeError("cannot reset while HITL is pending")
        baseline = getattr(self, "_session_baseline", None)
        if baseline:
            self.restore(baseline)
        ctx = self.driver.ctx
        reset_fn = getattr(ctx, "reset_conversation", None)
        if callable(reset_fn):
            reset_fn()
        engine = self.driver.engine
        seen: set[int] = set()
        for _ in range(8):
            if engine is None or id(engine) in seen:
                break
            seen.add(id(engine))
            reset_engine = existing_attr(engine, "reset_turn_state")
            if callable(reset_engine):
                reset_engine()
            inner = existing_attr(engine, "inner")
            if inner is None or inner is engine:
                break
            engine = inner

    def snapshot(self) -> dict:
        return self.kernel.snapshot()

    def restore(self, data: dict) -> None:
        self.kernel.restore(data)

    def record_checkpoint(self, label: str = "") -> dict:
        snap = self.snapshot()
        snap["label"] = label
        self._checkpoints.append(snap)
        return snap

    def load_checkpoint(self, data: dict) -> None:
        self.restore(data)

    @property
    def checkpoints(self) -> list[dict]:
        return list(self._checkpoints)
