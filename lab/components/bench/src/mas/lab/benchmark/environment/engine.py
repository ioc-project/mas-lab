#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""RunEnvironment: port, clock, timers, actions, event log."""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Mapping

from mas.lab.benchmark.environment.clock import RunClock
from mas.lab.benchmark.environment.events import NAME_RE, EventLog
from mas.lab.benchmark.environment.fixtures import FixtureRegistry
from mas.lab.benchmark.environment.model import (
    EnvironmentSpec,
    Limits,
    RunCoordinates,
    compile_environment,
)

__all__ = [
    "RunEnvironment",
    "compile_environment",
    "environment_digest",
    "merge_environment",
    "one_shot_context",
    "replay_environment_jsonl",
    "write_environment_control",
]
from mas.lab.benchmark.environment.step import (
    ActionResult,
    EnvironmentContext,
    instantiate_step,
)
from mas.lab.benchmark.environment.variables import Variables
from mas.library.standard.lib.environment_port import Anchor
from mas.library.standard.lib.triggers import StepSpec, Trigger, TriggerMatcher
from mas.runtime.aio import new_thread

logger = logging.getLogger(__name__)


def merge_environment(*blocks: Any) -> dict[str, Any]:
    """Deep-merge experiment → scenario → item environment mappings."""
    merged: dict[str, Any] = {}
    for block in blocks:
        if not block:
            continue
        if not isinstance(block, Mapping):
            raise ValueError("environment block must be a mapping")
        merged = _merge(merged, dict(block))
    return merged


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key in {"triggers", "timers", "fixtures"}:
            left = list(out.get(key) or [])
            right = list(value or [])
            out[key] = left + right
        elif key == "allow_network":
            left = list(out.get(key) or [])
            right = list(value or []) if not isinstance(value, str) else [value]
            out[key] = list(dict.fromkeys([*left, *right]))
        elif isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def environment_digest(spec: EnvironmentSpec | Mapping[str, Any] | None) -> str:
    """Stable digest of everything that changes what a run does.

    Empty environment returns '' so existing hashes stay put. The compiled spec is
    hashed whole (clock, triggers, actions, fixtures, limits, network, replay, script),
    so a field added to the model cannot silently stay out of the cache key.
    """
    compiled = spec if isinstance(spec, EnvironmentSpec) else compile_environment(spec)
    if (
        not compiled.triggers
        and not compiled.fixtures
        and not compiled.actions
        and not compiled.script
        and compiled.replay == "live"
        and compiled.clock.kind == "wall"
        and not compiled.raw
    ):
        return ""
    payload = dataclasses.asdict(compiled)
    payload.pop("raw", None)
    for fixture in payload["fixtures"]:
        fixture.pop("port", None)  # an allocated port differs per run; it is not a design choice
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:20]


def write_environment_control(path: Path, *, script: str = "") -> Path:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    spec: dict[str, Any] = {"governance": ["environment@v1"]}
    text = str(script or "").strip("\n")
    if text:
        # The plugin's own settings live on its governance entry, not under a top-level key.
        spec["governance"].append({"gdb": {"script": text + "\n"}})
    doc = {"apiVersion": "control/v1", "kind": "Control", "metadata": {"name": "environment"}, "spec": spec}
    path.write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False), encoding="utf-8")
    return path


class RunEnvironment:
    """Implements EnvironmentPort. Bound around an in-process MAS run."""

    def __init__(
        self,
        spec: EnvironmentSpec,
        *,
        run: RunCoordinates,
        path: Path | None = None,
        work_dir: Path | None = None,
        output_dir: Path | None = None,
        fixtures: FixtureRegistry | None = None,
        on_record: Any = None,
        control: Any = None,
        session_id: str = "",
        vars: Variables | None = None,
    ) -> None:
        self.spec = spec
        self.run = run
        self.clock = RunClock(spec.clock)
        self.limits = spec.limits
        self.allow_network = spec.allow_network
        self.vars = vars or Variables()
        self.control = control
        self.session_id = session_id
        self._entry_agent = ""
        self._bound_session = ""
        self._sessions: dict[str, tuple[Any, str]] = {}
        self.work_dir = Path(work_dir or ".")
        self._lock = threading.RLock()  # a step may re-enter through the run it controls
        self._inbox = threading.Lock()  # guards the two pending lists below
        self._timer_lock = threading.Lock()  # makes check-then-fire of a timer atomic
        self._timer_stop = threading.Event()
        self._timer_thread: threading.Thread | None = None
        self._unsub: Any = None
        self._replay_d3: list[dict[str, Any]] = []
        prior: list[dict[str, Any]] = []
        if spec.replay == "recorded" and path is not None and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    prior.append(rec)
            self._replay_d3 = [
                rec
                for rec in prior
                if rec.get("name") in {"event.emitted", "event.delivered", "timer.fired"} and rec.get("anchor")
            ]
        self.events = EventLog(
            path=path,
            clock=self.clock,
            run=run,
            limits=spec.limits,
            on_record=on_record,
            reset=not (spec.replay == "recorded" and prior),
        )
        if fixtures is None:
            self.fixtures = FixtureRegistry(
                output_dir=Path(output_dir or self.work_dir),
                work_dir=self.work_dir,
                emit=self.events.emit,
                vars=self.vars,
            )
        else:
            self.fixtures = fixtures
            fixtures.emit = self.events.emit  # fallback, last run wins; a scoped binding takes priority
        self.clock.on_advance(self._on_clock_advance)
        self._matcher = TriggerMatcher(spec.triggers)
        self._fired_at: dict[str, float] = {}
        self._skip_noted: set[str] = set()
        self._pending_obs: list[dict[str, str]] = []
        self._pending_events: list[str] = []
        self._recorded: list[tuple[str, Mapping[str, Any]]] = []
        self._closed = False
        self.fixture_keys: tuple[str, ...] = ()
        if not (spec.replay == "recorded" and prior):
            self.events.emit("clock.started", source={"type": "environment", "id": "clock"})
            for trigger in spec.triggers:
                if trigger.when.on == "timer":
                    self.events.emit(
                        "timer.armed",
                        source={"type": "timer", "id": trigger.id},
                        payload={"at_s": float(trigger.when.at_s or 0.0)},
                    )
        if self.clock.kind == "wall" and any(trigger.when.on == "timer" for trigger in spec.triggers):
            self._timer_thread = new_thread(target=self._wall_timer_loop, name="env-wall-timer", daemon=True)
            self._timer_thread.start()

    def triggers(self) -> tuple[Trigger, ...]:
        return self.spec.triggers

    def note_error(self, message: str) -> None:
        """Record that a plug point of this environment failed. The text stays out of the event log.

        It goes to the process log instead: a failed observer would otherwise leave only a bare
        ``action.failed`` with nothing to start a diagnosis from.
        """
        logger.warning("environment plug point failed: %s", message)
        try:
            self.events.emit(
                "action.failed",
                source={"type": "environment", "id": "environment"},
                outcome="failed",
                payload={"status": 0},
            )
        except Exception:
            logger.debug("environment note_error failed", exc_info=True)

    def observe(
        self,
        on: str,
        *,
        tool: str = "",
        agent: str = "",
        hook: str = "",
        op: str = "",
    ) -> None:
        self.on_transition(on, tool=tool, agent=agent, hook=hook, op=op)

    def bind_control(
        self,
        control: object | None,
        *,
        session_id: str = "",
        entry_agent: str = "",
    ) -> None:
        from mas.lab.benchmark.environment.control import EnvironmentControlAdapter

        adapter = None
        if control is not None:
            adapter = control if isinstance(control, EnvironmentControlAdapter) else EnvironmentControlAdapter(control)
        if session_id:
            self._sessions[session_id] = (adapter, entry_agent)
            if not self._bound_session:
                self._bound_session = session_id  # the first session bound is the run's own
        if not session_id or session_id == self._bound_session:
            # The default target: steps that name no session act on this one.
            if adapter is not None:
                self.control = adapter
            if session_id:
                self.session_id = session_id
            self._entry_agent = entry_agent

    def session_target(self, target: str | None = None) -> tuple[Any, str]:
        """``(control, session id)`` for a step's ``session:`` key.

        Empty means the run's own session. Otherwise a session id or the entry agent id of a
        session bound to this run; anything else is an error, never a silent fallback.
        """
        name = str(target or "").strip()
        if not name or name == self._bound_session:
            return self.control, self.session_id
        if name in self._sessions:
            adapter, _agent = self._sessions[name]
            return adapter, name
        for sid, (adapter, agent) in self._sessions.items():
            if agent and agent == name:
                return adapter, sid
        raise KeyError(f"environment has no bound session {name!r}; bound: {sorted(self._sessions) or 'none'}")

    def attach_event_stream(self, stream: Any) -> None:
        subscribe = None
        try:
            subscribe = stream.subscribe
        except AttributeError:
            return
        if not callable(subscribe):
            return
        if not any(trigger.when.on == "observability" for trigger in self.spec.triggers):
            return  # nothing would match: do not buffer every event of the run

        def _note(event: object) -> None:
            if not isinstance(event, Mapping):
                return
            row = {str(key): str(value) for key, value in event.items() if isinstance(value, (str, int, float, bool))}
            self.note_observability(row)

        self._unsub = subscribe(_note)

    def fire(self, trigger_id: str, anchor: Anchor) -> None:
        trigger = next((item for item in self.spec.triggers if item.id == trigger_id), None)
        if trigger is None or self._closed:
            return
        occurrence = int(anchor.occurrence or 0)
        self._fired_at[trigger.id] = self.clock.now()
        self.events.emit(
            "trigger.matched",
            source={"type": "trigger", "id": trigger.id},
            anchor=_anchor_dict(anchor),
            payload={"occurrence": occurrence},
        )
        self._run_steps(trigger.do, anchor=anchor, source_id=trigger.id)
        self._fire_due_timers(anchor)

    def fixture_url(self, name: str) -> str:
        for key in self.fixture_keys:
            try:
                return self.fixtures.url(name, scope_key=key)
            except KeyError:
                continue
        raise KeyError(f"unknown fixture {name!r}")

    def on_transition(
        self,
        on: str,
        *,
        tool: str = "",
        agent: str = "",
        hook: str = "",
        op: str = "",
        anchor: Any = None,
    ) -> None:
        if self._closed:
            return
        with self._lock:
            if self.clock.kind == "virtual":
                self.clock.tick(on)
            hits = self._matcher.match(on=on, tool=tool, agent=agent, entry_agent=self._entry_agent)
            exhausted = self._matcher.last_skipped  # a fired step can re-enter the matcher
            for trigger in hits:
                used = anchor or _SimpleAnchor(
                    on=on,
                    tool=tool,
                    agent=agent,
                    occurrence=self._matcher.count(trigger.id),
                    hook=hook,
                    op=op,
                )
                if self.spec.replay == "recorded":
                    self._replay_at_anchor(used)
                self.fire(trigger.id, used)
            for trigger in exhausted:
                if trigger.id in self._skip_noted:
                    continue  # once per trigger: later matches would flood the log
                self._skip_noted.add(trigger.id)
                self.events.emit(
                    "trigger.skipped",
                    source={"type": "trigger", "id": trigger.id},
                    outcome="skipped",
                )
            if self.spec.replay == "recorded" and anchor is not None:
                self._replay_at_anchor(anchor)
            self._deliver_pending(anchor)
            self._fire_due_timers(anchor)

    def emit_named(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.events.emit(
            name if str(name).startswith("user.") else f"user.{name}",
            source=source or {"type": "environment", "id": "emit"},
            payload=payload or {},
        )
        with self._inbox:
            self._pending_events.append(str(record.get("name") or name))
        return record

    def deliver_event(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> bool:
        folded = str(name or "").strip().lower()
        if folded and not folded.startswith("user."):
            folded = f"user.{folded}"
        event_id = folded or "user.event"
        src = source or {"type": "pipeline", "id": "pipeline"}
        if not NAME_RE.fullmatch(event_id):
            logger.warning("environment event name %r is not valid; dropped", name)
            return False
        body = {k: v for k, v in dict(payload or {}).items() if k != "event"}
        try:
            if self._closed:
                # A fold can outlive the run it feeds: record the miss if the log still takes it.
                self.events.emit(
                    "event.delivered",
                    source=src,
                    outcome="skipped",
                    payload={"event": event_id, "delivered": 0},
                )
                return False
            try:
                self.events.emit("event.emitted", source=src, payload={"event": event_id, **body})
            except ValueError:
                # The payload is not value-free: keep the event, drop the values.
                self.events.emit("event.emitted", source=src, payload={"event": event_id})
        except RuntimeError:
            return False  # log already closed: nothing left to deliver into
        with self._inbox:
            self._pending_events.append(event_id)
        return True

    def note_observability(self, row: Mapping[str, str]) -> None:
        with self._inbox:
            if len(self._pending_obs) >= _MAX_PENDING_OBS:
                self._pending_obs.pop(0)  # a run with no transitions for a while keeps the newest rows
            self._pending_obs.append({str(k): str(v) for k, v in row.items()})

    def run_named_action(self, name: str, *, anchor: Any = None) -> None:
        steps = self.spec.actions.get(name)
        if steps is None:
            raise KeyError(f"unknown environment action {name!r}")
        self._run_steps(steps, anchor=anchor, source_id=name)

    def context(self, *, anchor: Any = None) -> EnvironmentContext:
        return EnvironmentContext(
            run=self.run,
            clock=self.clock,
            fixtures=_ScopedFixtures(self),
            vars=self.vars,
            events=self.events,
            limits=self.limits,
            allow_network=self.allow_network,
            anchor=anchor,
            control=self.control,
            engine=self,
            work_dir=self.work_dir,
            session_id=self.session_id,
            base_env=getattr(self.fixtures, "base_env", None),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer_stop.set()
        if self._timer_thread is not None:
            self._timer_thread.join(timeout=1.0)
        self._note_undelivered()
        if self._unsub is not None:
            try:
                self._unsub()
            except Exception:
                logger.debug("environment stream unsubscribe failed", exc_info=True)
        if getattr(self.fixtures, "emit", None) is self.events.emit:
            self.fixtures.emit = None
        unbind = getattr(self.fixtures, "unbind_emit", None)
        if callable(unbind):
            for key in self.fixture_keys:
                unbind(key, self.events.emit)
        try:
            self.events.close()
        except Exception:
            logger.debug("environment close emit failed", exc_info=True)

    def _note_undelivered(self) -> None:
        """Events emitted after the last transition never reached a trigger: say so in the log."""
        with self._inbox:
            left, self._pending_events = self._pending_events, []
        for name in left:
            try:
                self.events.emit(
                    "event.delivered",
                    source={"type": "pipeline", "id": "pipeline"},
                    outcome="skipped",
                    payload={"event": name if str(name).startswith("user.") else f"user.{name}", "delivered": 0},
                )
            except (RuntimeError, ValueError):
                return

    def _wall_timer_loop(self) -> None:
        while not self._timer_stop.wait(0.05):
            if self._closed:
                return
            try:
                with self._lock:
                    if self._closed:
                        return
                    due = self._collect_due_timers(None)
                for trigger in due:
                    self._run_steps(trigger.do, anchor=None, source_id=trigger.id)
            except RuntimeError:
                return  # the log closed under us: the run is over
            except Exception:
                # One bad step must not end the loop: later timers still have to fire.
                logger.warning("environment wall timer step failed", exc_info=True)

    def _replay_at_anchor(self, anchor: Any) -> None:
        if not self._replay_d3:
            return
        try:
            occ = int(anchor.occurrence or 0)
        except AttributeError:
            occ = 0
        here = tuple((field, str(getattr(anchor, field, "") or "")) for field in ("op", "tool", "agent"))
        kept: list[dict[str, Any]] = []
        for rec in self._replay_d3:
            anc = rec.get("anchor") if isinstance(rec.get("anchor"), Mapping) else {}
            if _int(anc.get("occurrence")) != occ:
                kept.append(rec)
                continue
            # The occurrence number alone repeats across triggers: also match what is recorded.
            if any(want and str(anc.get(field) or "") and str(anc.get(field) or "") != want for field, want in here):
                kept.append(rec)
                continue
            payload = rec.get("payload") if isinstance(rec.get("payload"), Mapping) else {}
            source = rec.get("source") if isinstance(rec.get("source"), Mapping) else {}
            event = str(payload.get("event") or source.get("id") or "")
            if event:
                with self._inbox:
                    self._pending_events.append(event)
        self._replay_d3 = kept

    def _on_clock_advance(self, now_s: float) -> None:
        self.events.emit(
            "clock.advanced",
            source={"type": "environment", "id": "clock"},
            payload={"virtual_s": now_s},
        )
        self.fixtures.push_clock(self.clock.epoch_seconds(), scope_keys=self.fixture_keys)
        self._fire_due_timers(None)

    def _fire_due_timers(self, anchor: Any) -> None:
        for trigger in self._collect_due_timers(anchor):
            self._run_steps(trigger.do, anchor=anchor, source_id=trigger.id)

    def _collect_due_timers(self, anchor: Any) -> list[Trigger]:
        """Mark due timers as fired (and log it); the caller runs their steps.

        Splitting the two lets the wall-clock thread run steps without holding the engine lock:
        a step that calls the control API must not wait on a lock the run's own thread holds.

        Never take ``self._lock`` here. Steps run on a worker thread while the run's thread holds
        that lock and waits for them; a step that advances the clock arrives here, and would
        deadlock. The matcher and the event log have locks of their own.
        """
        due: list[Trigger] = []
        with self._timer_lock:  # held only while marking; no step runs inside it
            for trigger in self.spec.triggers:
                if trigger.when.on != "timer":
                    continue
                # A jump of the virtual clock can owe several periods: catch up, within a bound.
                for _ in range(_MAX_CATCH_UP):
                    if self._matcher.fires(trigger.id) >= trigger.max_fires:
                        break
                    if not self._timer_is_due(trigger):
                        break
                    self._matcher.record_fire(trigger.id)
                    self._fired_at[trigger.id] = self.clock.now()
                    self.events.emit(
                        "timer.fired",
                        source={"type": "timer", "id": trigger.id},
                        anchor=_anchor_dict(anchor) if anchor is not None else None,
                    )
                    due.append(trigger)
        return due

    def _timer_is_due(self, trigger: Trigger) -> bool:
        when = trigger.when
        base = 0.0
        if when.after:
            if self._matcher.fires(when.after) < 1:
                return False
            base = self._fired_at.get(when.after, 0.0)  # delay counts from the predecessor
        return _timer_due(trigger, self.clock.now(), self._matcher.fires(trigger.id), base=base)

    def _deliver_pending(self, anchor: Any) -> None:
        with self._inbox:
            events, self._pending_events = self._pending_events, []
        for name in events:
            folded = name if str(name).startswith("user.") else f"user.{name}"
            self.events.emit(
                "event.delivered",
                source={"type": "pipeline", "id": "pipeline"},
                payload={"event": folded, "delivered": 1},
                anchor=_anchor_dict(anchor) if anchor is not None else None,
            )
            hits = self._matcher.match(on="event", event=folded, entry_agent=self._entry_agent)
            for trigger in hits:
                used = anchor or _SimpleAnchor(
                    on="event",
                    tool="",
                    agent="",
                    occurrence=self._matcher.count(trigger.id),
                )
                self.fire(trigger.id, used)
        with self._inbox:
            rows, self._pending_obs = self._pending_obs, []
        for row in rows:
            hits = self._matcher.match(on="observability", observability=row, entry_agent=self._entry_agent)
            for trigger in hits:
                self._fired_at[trigger.id] = self.clock.now()
                self.events.emit(
                    "trigger.matched",
                    source={"type": "observability", "id": trigger.id},
                    anchor=_anchor_dict(anchor) if anchor is not None else None,
                )
                self._run_steps(trigger.do, anchor=anchor, source_id=trigger.id)

    def _run_steps(self, steps: tuple[StepSpec, ...], *, anchor: Any, source_id: str) -> None:
        if self._closed:
            return
        ctx = self.context(anchor=anchor)
        timeout = self.limits.action_timeout_s
        for spec in steps:
            self.events.emit(
                "action.started",
                source={"type": "trigger", "id": source_id, "step": spec.type},
                anchor=_anchor_dict(anchor) if anchor is not None else None,
            )
            try:
                result = _run_coro(_act(spec, ctx), timeout=timeout)
            except TimeoutError:
                logger.warning("environment action %s timed out after %ss", spec.type, timeout)
                self.events.emit(
                    "action.timeout",
                    source={"type": "trigger", "id": source_id, "step": spec.type},
                    outcome="timeout",
                    anchor=_anchor_dict(anchor) if anchor is not None else None,
                )
                continue
            except Exception as exc:
                logger.warning("environment action %s failed: %s", spec.type, exc, exc_info=True)
                self.events.emit(
                    "action.failed",
                    source={"type": "trigger", "id": source_id, "step": spec.type},
                    outcome="failed",
                    payload={"status": 0, "error": type(exc).__name__.lower()},
                    anchor=_anchor_dict(anchor) if anchor is not None else None,
                )
                continue
            outcome = result.outcome if isinstance(result, ActionResult) else "ok"
            payload = dict(result.payload) if isinstance(result, ActionResult) else {}
            name = "action.done" if outcome == "ok" else "action.failed"
            try:
                self.events.emit(
                    name,
                    source={"type": "trigger", "id": source_id, "step": spec.type},
                    outcome=outcome,
                    payload=payload,
                    anchor=_anchor_dict(anchor) if anchor is not None else None,
                )
            except ValueError:
                # A step returned a payload that is not value-free: keep the fact, drop the values.
                logger.warning("environment action %s returned a payload that is not value-free", spec.type)
                self.events.emit(
                    "action.failed",
                    source={"type": "trigger", "id": source_id, "step": spec.type},
                    outcome="failed",
                    payload={"status": 0, "error": "payload"},
                    anchor=_anchor_dict(anchor) if anchor is not None else None,
                )


async def _act(spec: StepSpec, ctx: EnvironmentContext) -> ActionResult:
    step = instantiate_step(spec)
    return await step.act(ctx)


def _run_coro(coro: Any, *, timeout: float) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))

    def worker() -> Any:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(worker).result(timeout=timeout + 1.0)


_MAX_CATCH_UP = 1000
_MAX_PENDING_OBS = 10_000


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0  # a damaged recorded line must not fail the live run


def _timer_due(trigger: Trigger, now: float, fired: int, *, base: float = 0.0) -> bool:
    when = trigger.when
    start = base + float(when.at_s or 0.0) + float(when.delay_s or 0.0)
    if when.every_s:
        return now + 1e-9 >= start + fired * float(when.every_s)
    return fired < 1 and now + 1e-9 >= start


def _anchor_dict(anchor: Any) -> dict[str, Any] | None:
    if anchor is None:
        return None
    return {
        "hook": str(anchor.hook or ""),
        "op": str(anchor.op or ""),
        "tool": str(anchor.tool or ""),
        "agent": str(anchor.agent or ""),
        "occurrence": int(anchor.occurrence or 0),
    }


def _str_field(obj: object, name: str) -> str:
    try:
        value = object.__getattribute__(obj, name)
    except AttributeError:
        return ""
    return str(value or "")


class _ScopedFixtures:
    """Fixture lookup that prefers this run's scope keys."""

    def __init__(self, env: RunEnvironment) -> None:
        self._env = env

    def url(self, name: str) -> str:
        return self._env.fixture_url(name)

    def port(self, name: str) -> int:
        for key in self._env.fixture_keys:
            try:
                return self._env.fixtures.port(name, scope_key=key)
            except KeyError:
                continue
        raise KeyError(f"unknown fixture {name!r}")

    def host(self, name: str) -> str:
        for key in self._env.fixture_keys:
            try:
                return self._env.fixtures.host(name, scope_key=key)
            except KeyError:
                continue
        raise KeyError(f"unknown fixture {name!r}")

    def _key_for(self, name: str) -> str | None:
        """The scope key under which this run registered ``name``, or None (never a sibling)."""
        for key in self._env.fixture_keys:
            if self._env.fixtures.has(name, scope_key=key):
                return key
        return None

    def restart(self, name: str) -> Any:
        key = self._key_for(name)
        if key is None:
            raise KeyError(f"unknown fixture {name!r}")
        return self._env.fixtures.restart(name, scope_key=key)

    def stop(self, name: str) -> None:
        key = self._key_for(name)
        if key is None:
            return
        self._env.fixtures.release(name, force=False, scope_key=key)


class _SimpleAnchor:
    def __init__(
        self,
        *,
        on: str,
        tool: str,
        agent: str,
        occurrence: int,
        hook: str = "",
        op: str = "",
    ) -> None:
        self.hook = hook or ("egress" if on in {"tool_call", "llm_call"} else "ingress")
        self.op = op or {
            "tool_call": "TOOL_CALL",
            "tool_result": "TOOL_RESULT",
            "llm_call": "LLM_CALL",
            "turn_start": "USER_INPUT_RECEIVED",
            "turn_end": "MODEL_TEXT",
            "event": "EVENT",
            "observability": "OBS",
            "timer": "TIMER",
        }.get(on, on.upper())
        self.tool = tool
        self.agent = agent
        self.occurrence = occurrence


def one_shot_context(pipeline_ctx: Any, *, config: Mapping[str, Any] | None = None) -> EnvironmentContext:
    """Reuse the live run environment when bound; otherwise a throwaway context."""
    del config
    from mas.library.standard.lib.environment_port import get_environment_port

    port = get_environment_port()
    if isinstance(port, RunEnvironment):
        return port.context()
    run = RunCoordinates(
        experiment=_str_field(pipeline_ctx, "experiment"),
        scenario=_str_field(pipeline_ctx, "scenario_id"),
    )
    spec = EnvironmentSpec(limits=Limits())
    env = RunEnvironment(spec, run=run, work_dir=Path(_str_field(pipeline_ctx, "base_dir") or "."))
    return env.context()


def replay_environment_jsonl(path: Path, emit: Any) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            emit({**rec, "kind": rec.get("kind") or "custom.environment"})
