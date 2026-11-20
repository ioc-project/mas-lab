#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared trigger model: structured YAML and gdb ``break`` lines compile here."""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

ON_KINDS = (
    "tool_call",
    "tool_result",
    "llm_call",
    "turn_start",
    "turn_end",
    "timer",
    "event",
    "observability",
)

OnKind = Literal[
    "tool_call",
    "tool_result",
    "llm_call",
    "turn_start",
    "turn_end",
    "timer",
    "event",
    "observability",
]

TRIGGER_ID_RE = re.compile(r"^[a-z0-9._-]{1,64}$")
DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)$")
_BREAK_RE = re.compile(
    r"^break\s+"
    r"(?P<on>tool_call|tool_result|llm_call|turn_start|turn_end)"
    r"(?:\s+(?P<tool>(?!agent\b|if\b)[A-Za-z0-9_.:-]+))?"
    r"(?:\s+agent\s+(?P<agent>\$entry|[A-Za-z0-9_.:-]+))?"
    r"(?:\s+if\s+(?P<cond>first|every|[0-9]+))?"
    r"$",
    re.IGNORECASE,
)


def parse_duration(text: str) -> float:
    """Parse ``21s`` / ``500ms`` / ``2m`` / ``1h`` into seconds."""
    raw = str(text or "").strip()
    matched = DURATION_RE.fullmatch(raw)
    if matched is None:
        raise ValueError(f"duration must look like 21s, 500ms, 2m or 1h, got {text!r}")
    value = float(matched.group(1))
    unit = matched.group(2)
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return value * scale


@dataclass(frozen=True)
class StepSpec:
    """One environment step in a trigger's ``do`` list."""

    type: str
    config: Mapping[str, Any] = field(default_factory=dict)
    name: str = ""


@dataclass(frozen=True)
class When:
    on: OnKind
    tool: str = ""
    agent: str = ""
    occurrence: int | Literal["first", "every"] = "first"
    at_s: float | None = None
    every_s: float | None = None
    after: str = ""
    delay_s: float = 0.0
    event: str = ""
    match: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Trigger:
    id: str
    when: When
    do: tuple[StepSpec, ...] = ()
    repeat: bool = False
    max_fires: int = 1


def _occurrence(raw: Any) -> int | Literal["first", "every"]:
    if raw is None or raw == "":
        return "first"
    if raw is True:
        return "every"
    if isinstance(raw, int):
        if raw < 1:
            raise ValueError(f"occurrence must be >= 1, got {raw}")
        return raw
    text = str(raw).strip().lower()
    if text in {"first", "every"}:
        return text  # type: ignore[return-value]
    if text.isdigit():
        value = int(text)
        if value < 1:
            raise ValueError(f"occurrence must be >= 1, got {raw}")
        return value
    raise ValueError(f"occurrence must be first, every, or a positive integer, got {raw!r}")


def parse_break_line(line: str) -> When:
    """Parse ``break tool_result pay_fare if 2`` (gdb surface) into a :class:`When`."""
    stripped = str(line or "").split("#", 1)[0].strip()
    matched = _BREAK_RE.fullmatch(stripped)
    if matched is None:
        raise ValueError(
            f"break line must look like 'break tool_result NAME [agent ID] [if first|every|N]', got {line!r}"
        )
    on = matched.group("on").lower().replace("-", "_")
    cond = matched.group("cond")
    occurrence: int | Literal["first", "every"] = "first"
    if cond is not None:
        occurrence = _occurrence(cond)
        if occurrence == "first" and on != "tool_result":
            raise ValueError("if first is only valid on break tool_result")
    return When(
        on=on,  # type: ignore[arg-type]
        tool=str(matched.group("tool") or ""),
        agent=str(matched.group("agent") or ""),
        occurrence=occurrence,
    )


def _when_on(raw: Mapping[str, Any]) -> Any:
    """Read ``on``; YAML 1.1 turns the key ``on:`` into boolean ``True``."""
    if "on" in raw:
        return raw["on"]
    if True in raw:
        return raw[True]
    return None


def compile_when(raw: Any) -> When:
    if isinstance(raw, When):
        return raw
    if isinstance(raw, str):
        return parse_break_line(raw)
    if not isinstance(raw, Mapping):
        raise ValueError(f"when must be a mapping or a break line, got {type(raw).__name__}")
    on_raw = _when_on(raw)
    on = str(on_raw or "").strip().lower().replace("-", "_")
    if on not in ON_KINDS:
        raise ValueError(f"when.on must be one of {ON_KINDS}, got {on_raw!r}")
    at_s = raw.get("at_s")
    if at_s is None and raw.get("at") is not None:
        at_s = parse_duration(str(raw["at"]))
    every_s = raw.get("every_s")
    if every_s is None and raw.get("every") is not None:
        every_s = parse_duration(str(raw["every"]))
    delay_s = raw.get("delay_s")
    if delay_s is None and raw.get("delay") is not None:
        delay_s = parse_duration(str(raw["delay"]))
    match_raw = raw.get("match") or {}
    if match_raw and not isinstance(match_raw, Mapping):
        raise ValueError("when.match must be a mapping")
    after = str(raw.get("after") or "")
    if on == "timer":
        if at_s is None and every_s is None and not after and delay_s is None:
            raise ValueError("a timer needs at, every, delay or after")
    elif at_s is not None or every_s is not None or after or delay_s is not None:
        raise ValueError(f"at, every, delay and after only apply to timers, not to on: {on}")
    for label, value in (("at", at_s), ("every", every_s), ("delay", delay_s)):
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"when.{label} must be a finite number, got {value!r}")
    if at_s is not None and float(at_s) < 0:
        raise ValueError("when.at must be >= 0")
    if every_s is not None and float(every_s) <= 0:
        raise ValueError("when.every must be > 0")
    if delay_s is not None and float(delay_s) < 0:
        raise ValueError("when.delay must be >= 0")
    event = str(raw.get("event") or "").strip().lower()
    if event and not event.startswith("user."):
        event = f"user.{event}"  # deliver_event folds every name the same way
    return When(
        on=on,  # type: ignore[arg-type]
        tool=str(raw.get("tool") or ""),
        agent=str(raw.get("agent") or ""),
        occurrence=_occurrence(raw.get("occurrence", "first")),
        at_s=float(at_s) if at_s is not None else None,
        every_s=float(every_s) if every_s is not None else None,
        after=after,
        delay_s=float(delay_s or 0.0),
        event=event,
        match={str(k): str(v) for k, v in dict(match_raw).items()},
    )


def compile_step(raw: Any) -> StepSpec:
    if isinstance(raw, StepSpec):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError(f"environment step must be a mapping, got {type(raw).__name__}")
    step_type = str(raw.get("type") or "").strip()
    if not step_type:
        raise ValueError("environment step requires type")
    config = raw.get("config") or {}
    if not isinstance(config, Mapping):
        raise ValueError("environment step config must be a mapping")
    return StepSpec(type=step_type, config=dict(config), name=str(raw.get("name") or ""))


def compile_trigger(raw: Mapping[str, Any], *, default_id: str = "") -> Trigger:
    trigger_id = str(raw.get("id") or default_id).strip()
    if not TRIGGER_ID_RE.fullmatch(trigger_id):
        raise ValueError(f"trigger id must match [a-z0-9._-]{{1,64}}, got {raw.get('id')!r}")
    when = compile_when(raw.get("when"))
    do_raw = raw.get("do") or ()
    if not isinstance(do_raw, (list, tuple)):
        raise ValueError("trigger.do must be a list of steps")
    do = tuple(compile_step(item) for item in do_raw)
    repeat = bool(raw.get("repeat")) or when.occurrence == "every" or when.every_s is not None
    if "max_fires" in raw:
        max_fires = int(raw["max_fires"])
    else:
        max_fires = 10_000 if repeat else 1
    if max_fires < 1:
        raise ValueError("max_fires must be >= 1")
    return Trigger(id=trigger_id, when=when, do=do, repeat=repeat, max_fires=max_fires)


def compile_timer(raw: Mapping[str, Any], *, default_id: str = "") -> Trigger:
    """A timer is a trigger whose ``when.on`` is ``timer``."""
    trigger_id = str(raw.get("id") or default_id).strip()
    payload = dict(raw)
    when_raw = dict(payload.get("when") or {})
    when_raw.setdefault("on", "timer")
    if "at" in payload and "at" not in when_raw and "at_s" not in when_raw:
        when_raw["at"] = payload["at"]
    if "every" in payload and "every" not in when_raw and "every_s" not in when_raw:
        when_raw["every"] = payload["every"]
    if "after" in payload and "after" not in when_raw:
        when_raw["after"] = payload["after"]
    if "delay" in payload and "delay" not in when_raw:
        when_raw["delay"] = payload["delay"]
    payload["when"] = when_raw
    return compile_trigger(payload, default_id=trigger_id)


def compile_triggers(
    triggers: Any = None,
    timers: Any = None,
) -> tuple[Trigger, ...]:
    compiled: list[Trigger] = []
    for index, raw in enumerate(triggers or ()):
        if not isinstance(raw, Mapping):
            raise ValueError("each trigger must be a mapping")
        compiled.append(compile_trigger(raw, default_id=f"trigger-{index + 1}"))
    for index, raw in enumerate(timers or ()):
        if not isinstance(raw, Mapping):
            raise ValueError("each timer must be a mapping")
        compiled.append(compile_timer(raw, default_id=f"timer-{index + 1}"))
    seen: set[str] = set()
    for trigger in compiled:
        if trigger.id in seen:
            raise ValueError(f"duplicate trigger id {trigger.id!r}")
        seen.add(trigger.id)
    for trigger in compiled:
        after = trigger.when.after
        if after and after not in seen:
            raise ValueError(f"trigger {trigger.id!r}: after names unknown trigger {after!r}")
        if after == trigger.id:
            raise ValueError(f"trigger {trigger.id!r} cannot come after itself")
    return tuple(compiled)


class TriggerMatcher:
    """Occurrence counting per run. A non-repeat trigger fires at most once per match."""

    def __init__(self, triggers: tuple[Trigger, ...]):
        self.triggers = triggers
        self._counts: dict[str, int] = {}
        self._fires: dict[str, int] = {}
        self.last_skipped: tuple[Trigger, ...] = ()
        # One matcher can see transitions from several agent threads: counts must not race.
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._fires.clear()
            self.last_skipped = ()

    def _wanted(self, trigger: Trigger, count: int) -> bool:
        occurrence = trigger.when.occurrence
        if trigger.repeat or occurrence == "every":
            return True
        if occurrence in {"first", 1}:
            return count == 1
        return count == int(occurrence)

    def match(
        self,
        *,
        on: str,
        tool: str = "",
        agent: str = "",
        event: str = "",
        observability: Mapping[str, str] | None = None,
        entry_agent: str = "",
    ) -> tuple[Trigger, ...]:
        with self._lock:
            hits: list[Trigger] = []
            skipped: list[Trigger] = []
            self.last_skipped = ()
            for trigger in self.triggers:
                when = trigger.when
                if when.on != on:
                    continue
                if when.tool and when.tool != tool:
                    continue
                wanted_agent = when.agent
                if wanted_agent == "$entry":
                    if not entry_agent:
                        continue
                    wanted_agent = entry_agent
                if wanted_agent and wanted_agent != agent:
                    continue
                if when.on == "event" and when.event and when.event != event:
                    continue
                if when.on == "observability" and when.match:
                    row = observability or {}
                    if any(row.get(key) != value for key, value in when.match.items()):
                        continue
                count = self._counts.get(trigger.id, 0) + 1
                self._counts[trigger.id] = count
                fired = self._fires.get(trigger.id, 0)
                if not self._wanted(trigger, count):
                    if fired >= trigger.max_fires:
                        skipped.append(trigger)  # matched the filters after it was spent
                    continue
                if fired >= trigger.max_fires:
                    skipped.append(trigger)  # matched every filter, but it has used up its fires
                    continue
                self._fires[trigger.id] = fired + 1
                hits.append(trigger)
            self.last_skipped = tuple(skipped)
            return tuple(hits)

    def count(self, trigger_id: str) -> int:
        with self._lock:
            return int(self._counts.get(trigger_id, 0))

    def fires(self, trigger_id: str) -> int:
        with self._lock:
            return int(self._fires.get(trigger_id, 0))

    def record_fire(self, trigger_id: str) -> int:
        with self._lock:
            n = self._fires.get(trigger_id, 0) + 1
            self._fires[trigger_id] = n
            return n
