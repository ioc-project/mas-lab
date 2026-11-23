#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compiled environment block: clock, limits, fixtures, run coordinates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from mas.library.standard.lib.triggers import StepSpec, Trigger, compile_step, compile_triggers

FIXTURE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
ClockKind = Literal["wall", "virtual"]
FixtureKind = Literal["process", "service", "step"]
FixtureScope = Literal["experiment", "scenario", "item", "run"]


@dataclass(frozen=True)
class RunCoordinates:
    experiment: str = ""
    scenario: str = ""
    item: str = ""
    run: str = ""
    attempt: int = 1


@dataclass(frozen=True)
class Limits:
    max_sleep_s: float = 60.0
    action_timeout_s: float = 30.0
    max_events_per_run: int = 10_000
    max_http_body_bytes: int = 65_536


@dataclass(frozen=True)
class HealthSpec:
    path: str = "/health"
    timeout_s: float = 15.0
    expect_status: int = 200


@dataclass(frozen=True)
class ClockBinding:
    path: str = "/clock"


@dataclass(frozen=True)
class ClockSpec:
    kind: ClockKind = "wall"
    start_s: float = 0.0
    epoch: str = "2026-01-01T00:00:00Z"
    tick: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    scope: FixtureScope = "run"
    kind: FixtureKind = "process"
    command: tuple[str, ...] = ()
    service: str = ""
    infra: str = ""
    start: StepSpec | None = None
    stop: StepSpec | None = None
    port: int | Literal["auto"] | None = None
    health: HealthSpec | None = None
    clock: ClockBinding | None = None
    watch: tuple[str, ...] = ()
    env_mode: Literal["inherit", "minimal"] = "inherit"
    stop_grace_s: float = 5.0
    keep_running: bool = False
    env: Mapping[str, Any] = field(default_factory=dict)  # strings or SecretRefs, revealed at spawn
    passthrough: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnvironmentSpec:
    clock: ClockSpec = field(default_factory=ClockSpec)
    triggers: tuple[Trigger, ...] = ()
    fixtures: tuple[FixtureSpec, ...] = ()
    limits: Limits = field(default_factory=Limits)
    allow_network: tuple[str, ...] = ()
    actions: Mapping[str, tuple[StepSpec, ...]] = field(default_factory=dict)
    script: str = ""
    replay: str = "live"
    raw: Mapping[str, Any] = field(default_factory=dict)


def _duration_map(raw: Any) -> dict[str, float]:
    from mas.library.standard.lib.triggers import parse_duration

    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("clock.tick must be a mapping of event to duration")
    out: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
        else:
            out[str(key)] = parse_duration(str(value))
    return out


def compile_clock(raw: Any) -> ClockSpec:
    from mas.library.standard.lib.triggers import parse_duration

    if not raw:
        return ClockSpec()
    if not isinstance(raw, Mapping):
        raise ValueError("environment.clock must be a mapping")
    kind = str(raw.get("kind") or "wall").strip().lower()
    if kind not in {"wall", "virtual"}:
        raise ValueError(f"clock.kind must be wall or virtual, got {raw.get('kind')!r}")
    start = raw.get("start_s", raw.get("start", 0.0))
    if start in (None, ""):
        start_s = 0.0
    elif isinstance(start, (int, float)):
        start_s = float(start)
    else:
        start_s = parse_duration(str(start))
    return ClockSpec(
        kind=kind,  # type: ignore[arg-type]
        start_s=start_s,
        epoch=str(raw.get("epoch") or "2026-01-01T00:00:00Z"),
        tick=_duration_map(raw.get("tick") or {}),
    )


def _positive(label: str, value: Any, *, integer: bool = False) -> Any:
    """A limit or timeout is a finite number above zero: 0, a negative or NaN would fail every run."""
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number, got {value!r}") from None
    if not number > 0 or number in (float("inf"),):
        raise ValueError(f"{label} must be a finite number above zero, got {value!r}")
    return number


def compile_limits(raw: Any) -> Limits:
    if not raw:
        return Limits()
    if not isinstance(raw, Mapping):
        raise ValueError("environment.limits must be a mapping")
    return Limits(
        max_sleep_s=_positive("limits.max_sleep_s", raw.get("max_sleep_s", 60.0)),
        action_timeout_s=_positive("limits.action_timeout_s", raw.get("action_timeout_s", 30.0)),
        max_events_per_run=_positive("limits.max_events_per_run", raw.get("max_events_per_run", 10_000), integer=True),
        max_http_body_bytes=_positive(
            "limits.max_http_body_bytes", raw.get("max_http_body_bytes", 65_536), integer=True
        ),
    )


def _url_path(raw: Any, default: str) -> str:
    """A probe path always starts with a slash: ``health`` would otherwise glue onto the port."""
    text = str(raw or default).strip()
    return text if text.startswith("/") else "/" + text


def compile_fixture(raw: Mapping[str, Any]) -> FixtureSpec:
    name = str(raw.get("name") or "").strip()
    if not FIXTURE_NAME_RE.fullmatch(name):
        raise ValueError(f"fixture name must match [a-z0-9_-]{{1,64}}, got {raw.get('name')!r}")
    kind = str(raw.get("kind") or "process").strip().lower()
    if kind not in {"process", "service", "step"}:
        raise ValueError(f"fixture.kind must be process, service or step, got {raw.get('kind')!r}")
    scope = str(raw.get("scope") or "run").strip().lower()
    if scope not in {"experiment", "scenario", "item", "run"}:
        raise ValueError(f"fixture.scope must be experiment, scenario, item or run, got {raw.get('scope')!r}")
    command_raw = raw.get("command") or ()
    if isinstance(command_raw, str):
        import shlex

        command = tuple(shlex.split(command_raw))
    else:
        command = tuple(str(part) for part in command_raw)
    port_raw = raw.get("port")
    port: int | Literal["auto"] | None
    if port_raw in (None, ""):
        port = None
    elif str(port_raw).strip().lower() == "auto":
        port = "auto"
    else:
        port = int(port_raw)
        if not 1 <= port <= 65535:
            raise ValueError(f"fixture {name!r}: port must be 1 to 65535 or auto, got {port_raw!r}")
    health_raw = raw.get("health")
    health = None
    if isinstance(health_raw, Mapping):
        health = HealthSpec(
            path=_url_path(health_raw.get("path"), "/health"),
            timeout_s=_positive(f"fixture {name!r}: health.timeout_s", health_raw.get("timeout_s", 15.0)),
            expect_status=int(health_raw.get("expect_status", 200)),
        )
    clock_raw = raw.get("clock")
    clock = None
    if isinstance(clock_raw, Mapping):
        clock = ClockBinding(path=_url_path(clock_raw.get("path"), "/clock"))
    elif clock_raw:
        clock = ClockBinding()
    start = compile_step(raw["start"]) if raw.get("start") else None
    stop = compile_step(raw["stop"]) if raw.get("stop") else None
    if kind == "step" and (start is None or stop is None):
        raise ValueError(f"fixture {name!r}: kind step requires start and stop")
    watch = raw.get("watch") or ()
    if isinstance(watch, str):
        watch = (watch,)
    if not isinstance(watch, (list, tuple)):
        raise ValueError(f"fixture {name!r}: watch must be a path or a list of paths")
    env_mode = str(raw.get("env_mode") or "inherit").strip().lower()
    if env_mode not in {"inherit", "minimal"}:
        raise ValueError(f"fixture {name!r}: env_mode must be inherit or minimal, got {raw.get('env_mode')!r}")
    from mas.lab.benchmark.environment.declared_env import compile_env

    env = compile_env(raw.get("env") or {}, where=f"fixture {name!r}")
    passthrough = raw.get("passthrough") or ()
    if isinstance(passthrough, str):
        passthrough = (passthrough,)
    return FixtureSpec(
        name=name,
        scope=scope,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        command=command,
        service=str(raw.get("service") or ""),
        infra=str(raw.get("infra") or ""),
        start=start,
        stop=stop,
        port=port,
        health=health,
        clock=clock,
        watch=tuple(str(item) for item in watch),
        env_mode=env_mode,  # type: ignore[arg-type]
        stop_grace_s=_positive(f"fixture {name!r}: stop_grace_s", raw.get("stop_grace_s", 5.0)),
        keep_running=bool(raw.get("keep_running")),
        env=env,
        passthrough=tuple(str(item) for item in passthrough),
    )


def compile_environment(raw: Any) -> EnvironmentSpec:
    from mas.runtime.secrets import prepare_manifest

    if not raw:
        return EnvironmentSpec()
    if not isinstance(raw, Mapping):
        raise ValueError("environment must be a mapping")
    raw = prepare_manifest(raw)
    fixtures_raw = raw.get("fixtures") or ()
    if fixtures_raw and not isinstance(fixtures_raw, (list, tuple)):
        raise ValueError("environment.fixtures must be a list")
    fixtures = tuple(compile_fixture(item) for item in fixtures_raw)
    names = [item.name for item in fixtures]
    if len(names) != len(set(names)):
        raise ValueError("duplicate fixture name in environment")
    allow = raw.get("allow_network") or ()
    if isinstance(allow, str):
        allow = [allow]
    actions_raw = raw.get("actions") or {}
    if actions_raw and not isinstance(actions_raw, Mapping):
        raise ValueError("environment.actions must be a mapping of name to step lists")
    actions: dict[str, tuple[StepSpec, ...]] = {}
    for key, steps in dict(actions_raw).items():
        if not isinstance(steps, (list, tuple)):
            raise ValueError(f"environment.actions.{key} must be a list of steps")
        actions[str(key)] = tuple(compile_step(step) for step in steps)
    replay = str(raw.get("replay") or "live").strip().lower()
    if replay not in {"live", "recorded"}:
        raise ValueError("environment.replay must be live or recorded")
    spec = EnvironmentSpec(
        clock=compile_clock(raw.get("clock")),
        triggers=compile_triggers(raw.get("triggers"), raw.get("timers")),
        fixtures=fixtures,
        limits=compile_limits(raw.get("limits")),
        allow_network=tuple(str(item) for item in allow),
        actions=actions,
        script=str(raw.get("script") or ""),
        replay=replay,
        raw=dict(raw),
    )
    _require_known_steps(spec)
    return spec


def _require_known_steps(spec: EnvironmentSpec) -> None:
    """Refuse unknown ``do`` / action / fixture step types when the environment compiles."""
    from mas.lab.benchmark.environment.step import instantiate_step

    steps: list[StepSpec] = []
    for trigger in spec.triggers:
        steps.extend(trigger.do)
    for named in spec.actions.values():
        steps.extend(named)
    for fixture in spec.fixtures:
        if fixture.start is not None:
            steps.append(fixture.start)
        if fixture.stop is not None:
            steps.append(fixture.stop)
    seen: set[str] = set()
    for step in steps:
        if step.type in seen:
            continue
        seen.add(step.type)
        try:
            instantiate_step(step)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
