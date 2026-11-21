#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""EnvironmentStep: a world action, also usable as a pre/post pipeline step."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from mas.lab.benchmark.environment.clock import RunClock
from mas.lab.benchmark.environment.events import EventLog
from mas.lab.benchmark.environment.model import Limits, RunCoordinates
from mas.lab.benchmark.environment.variables import Variables
from mas.lab.benchmark.pipeline.core import PipelineStep, StepOutput
from mas.library.standard.lib.triggers import StepSpec


class FixtureView(Protocol):
    def url(self, name: str) -> str: ...

    def port(self, name: str) -> int: ...

    def host(self, name: str) -> str: ...

    def restart(self, name: str) -> Any: ...

    def stop(self, name: str) -> None: ...


class EnvironmentControl(Protocol):
    def steer(self, session_id: str, **kwargs: Any) -> Any: ...

    def enqueue_input(self, session_id: str, *, text: str, **kwargs: Any) -> Any: ...

    def pause(self, session_id: str, **kwargs: Any) -> Any: ...

    def resume(self, session_id: str, **kwargs: Any) -> Any: ...

    def snapshot(self, session_id: str, **kwargs: Any) -> Any: ...

    def persist(self, session_id: str, **kwargs: Any) -> Any: ...

    def cancel_inflight(self, session_id: str, **kwargs: Any) -> Any: ...

    def disable_tool(self, session_id: str, *, name: str, **kwargs: Any) -> Any: ...

    def enable_tool(self, session_id: str, *, name: str, **kwargs: Any) -> Any: ...


@dataclass
class ActionResult:
    outcome: str = "ok"
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class EnvironmentContext:
    run: RunCoordinates
    clock: RunClock
    fixtures: FixtureView
    vars: Variables
    events: EventLog
    limits: Limits
    allow_network: tuple[str, ...] = ()
    anchor: Any = None
    control: EnvironmentControl | None = None
    engine: Any = None
    work_dir: Any = None
    session_id: str = ""
    base_env: Any = None  # the environment a spawned process may inherit; the CLI supplies it

    def interpolate(self, value: Any) -> Any:
        return self.vars.interpolate_any(value)

    def target(self, session: Any = None, *, capability: str) -> tuple[Any, str]:
        """``(control, session id)`` for a step; ``session:`` names another bound session.

        A step with no control bound is refused here, with the capability it needed.
        """
        if self.engine is not None:
            control, session_id = self.engine.session_target(None if session is None else str(session))
        else:
            control, session_id = self.control, self.session_id
        if control is None:
            raise PermissionError(f"environment control cannot {capability}: capability refused")
        return control, session_id


class EnvironmentStep(PipelineStep):
    """World action. ``act`` runs inside a trigger; ``execute`` is the one-shot path."""

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        raise NotImplementedError

    async def execute(self, ctx: Any) -> StepOutput:
        from mas.lab.benchmark.environment.engine import one_shot_context

        env = one_shot_context(ctx, config=self.config)
        result = await self.act(env)
        return StepOutput(metadata={"outcome": result.outcome, **result.payload})


_BUILTIN_STEPS = {
    "http_call": "mas.library.lab.steps.environment.http_call:HttpCallStep",
    "clock_advance": "mas.library.lab.steps.environment.clock_advance:ClockAdvanceStep",
    "sleep": "mas.library.lab.steps.environment.sleep:SleepStep",
    "shell": "mas.library.lab.steps.environment.shell_action:ShellActionStep",
    "emit": "mas.library.lab.steps.environment.emit:EmitStep",
    "fixture_restart": "mas.library.lab.steps.environment.fixture_control:FixtureRestartStep",
    "fixture_stop": "mas.library.lab.steps.environment.fixture_control:FixtureStopStep",
    "steer": "mas.library.lab.steps.environment.steer:SteerStep",
    "inject": "mas.library.lab.steps.environment.inject:InjectStep",
    "pause": "mas.library.lab.steps.environment.pause:PauseStep",
    "resume": "mas.library.lab.steps.environment.resume:ResumeStep",
    "checkpoint": "mas.library.lab.steps.environment.checkpoint:CheckpointStep",
    "kill_switch": "mas.library.lab.steps.environment.kill_switch:KillSwitchStep",
    "hitl_answer": "mas.library.lab.steps.environment.hitl_answer:HitlAnswerStep",
    "threshold_alarm": "mas.library.lab.steps.environment.threshold_alarm:ThresholdAlarmStep",
}


def _load_builtin(type_name: str) -> type[EnvironmentStep] | None:
    token = _BUILTIN_STEPS.get(type_name)
    if not token:
        return None
    module_name, _, attr = token.partition(":")
    import importlib

    module = importlib.import_module(module_name)
    cls = object.__getattribute__(module, attr)
    if not isinstance(cls, type):
        return None
    return cls


def instantiate_step(spec: StepSpec) -> EnvironmentStep:
    cls: type | None = None
    lookup_error: Exception | None = None
    try:
        from mas.lab.benchmark.pipeline.core import resolve_step_class

        cls = resolve_step_class(spec.type, required_base=PipelineStep)
    except (KeyError, LookupError, TypeError, ValueError) as exc:
        cls, lookup_error = None, exc  # not a registered pipeline step: fall back to the built-ins
    if cls is None or not isinstance(cls, type) or not issubclass(cls, EnvironmentStep):
        cls = _load_builtin(spec.type)
    if cls is None:
        detail = f": {lookup_error}" if lookup_error is not None else ""
        raise TypeError(f"unknown environment step type {spec.type!r}{detail}")
    step = cls(name=spec.name or spec.type, config=dict(spec.config))
    if not isinstance(step, EnvironmentStep):
        raise TypeError(f"step type {spec.type!r} is not an EnvironmentStep")
    return step
