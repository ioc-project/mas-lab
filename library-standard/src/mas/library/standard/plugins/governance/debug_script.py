#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Gdb-like debug scripts as a governance plugin.

Breakpoints observe tool-call / tool-result transitions and act through the
ControlContract (checkpoint, inspect, pause, resume). ``evaluate_egress`` always
PASSes: the plugin never blocks. Name it in a Control ``spec.governance`` with
``script`` or ``script_file``; ``mas-ctl chat --debug-script`` writes that Control.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TextIO

from mas.runtime.boundary.gov.filter import GovTransitionFilter
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.session import SessionStatus

PLUGIN_ID = "gdb"

BreakpointKind = Literal["tool_call", "tool_result", "llm_call", "turn_start", "turn_end"]


@dataclass(frozen=True)
class Breakpoint:
    kind: BreakpointKind
    tool_name: str = ""
    first_only: bool = False
    commands: tuple[str, ...] = ()
    agent: str = ""
    occurrence: int | Literal["first", "every"] | None = None


@dataclass
class MemoryCheckpoint:
    checkpoint_id: str
    session_id: str
    reason: str
    hook: str
    tool_name: str
    q_state: dict[str, Any] = field(default_factory=dict)
    working_memory: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "reason": self.reason,
            "hook": self.hook,
            "tool_name": self.tool_name,
            "q_state": dict(self.q_state),
            "working_memory": list(self.working_memory),
            "attributes": dict(self.attributes),
        }


def parse_debug_script(text: str) -> list[Breakpoint]:
    """Parse a gdb-like script into breakpoints with command lists."""
    lines = text.splitlines()
    breakpoints: list[Breakpoint] = []
    pending: Breakpoint | None = None
    collecting: list[str] | None = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        stripped = raw.split("#", 1)[0].strip()
        if not stripped:
            continue
        if collecting is not None:
            if stripped.lower() == "end":
                if pending is None:
                    raise ValueError("commands/end without a breakpoint")
                pending = Breakpoint(
                    kind=pending.kind,
                    tool_name=pending.tool_name,
                    first_only=pending.first_only,
                    commands=tuple(collecting),
                    agent=pending.agent,
                    occurrence=pending.occurrence,
                )
                breakpoints.append(pending)
                pending = None
                collecting = None
                continue
            collecting.append(stripped)
            continue
        tokens = stripped.split()
        verb = tokens[0].lower()
        if verb == "break":
            if pending is not None:
                breakpoints.append(pending)
            pending = _parse_break(tokens[1:])
            continue
        if verb == "commands":
            if pending is None:
                raise ValueError("commands without a preceding break")
            collecting = []
            continue
        raise ValueError(f"unknown debug-script command {stripped!r}")
    if collecting is not None:
        raise ValueError("commands block is missing end")
    if pending is not None:
        breakpoints.append(pending)
    return breakpoints


def _parse_break(rest: list[str]) -> Breakpoint:
    if not rest:
        raise ValueError("break requires tool_call, tool_result, llm_call, turn_start or turn_end")
    from mas.library.standard.lib.triggers import parse_break_line

    when = parse_break_line("break " + " ".join(rest))
    kind_token = when.on
    if kind_token not in {"tool_call", "tool_result", "llm_call", "turn_start", "turn_end"}:
        raise ValueError(
            f"break target must be tool_call, tool_result, llm_call, turn_start or turn_end, got {rest[0]!r}"
        )
    has_if = any(tok.lower() == "if" for tok in rest)
    first_only = bool(has_if and when.occurrence in {"first", 1})
    if first_only and kind_token != "tool_result":
        raise ValueError("if first is only valid on break tool_result")
    occurrence: int | Literal["first", "every"] | None
    if not has_if:
        occurrence = "every"
    else:
        occurrence = when.occurrence
    return Breakpoint(
        kind=kind_token,  # type: ignore[arg-type]
        tool_name=when.tool,
        first_only=first_only,
        agent=when.agent,
        occurrence=occurrence,
    )


def _read_script_file(script_file: str, *, manifest_dir: str = "") -> str:
    """Load a gdb-like script from ``script_file``.

    A relative path is resolved against ``manifest_dir`` (the agent dir). A
    Control attach rewrites ``script_file`` to an absolute path first, so a
    script next to the Control file is found.
    """
    path = Path(script_file)
    tried = [path]
    if not path.is_file() and manifest_dir and not path.is_absolute():
        nested = Path(manifest_dir) / script_file
        tried.append(nested)
        path = nested
    if not path.is_file():
        shown = ", ".join(str(item) for item in tried)
        raise FileNotFoundError(f"debug script not found ({shown})")
    return path.read_text(encoding="utf-8")


def _transition_tool_name(transition: Any) -> str:
    attrs = getattr(transition, "attributes", None) or {}
    if isinstance(attrs, dict):
        name = str(attrs.get("tool_name") or attrs.get("name") or "").strip()
        if name:
            return name
    return ""


def _is_tool_call(transition: Any) -> bool:
    return getattr(transition, "hook", "") == "egress" and getattr(transition, "op", "") == "TOOL_CALL"


def _is_tool_result(transition: Any) -> bool:
    return getattr(transition, "hook", "") == "ingress" and getattr(transition, "response_kind", "") == "TOOL_RESULT"


class DebugScriptPlugin:
    """Interpret a gdb-like script at governance transitions."""

    plugin_id = "gdb@v1"

    def __init__(
        self,
        script: str = "",
        script_file: str = "",
        *,
        out: TextIO | None = None,
        manifest_dir: str = "",
        **_raw: object,
    ) -> None:
        text = str(script or "")
        if text.startswith("@"):
            script_file = text[1:]
            text = ""
        if script_file:
            text = _read_script_file(str(script_file), manifest_dir=str(manifest_dir or ""))
        self.script_file = str(script_file or "")
        self.breakpoints = parse_debug_script(text) if text.strip() else []
        self.out = out if out is not None else sys.stderr
        self.checkpoints: list[MemoryCheckpoint] = []
        self.log: list[str] = []
        self._session: Any = None
        self._control: Any = None
        self._manager: Any = None
        self._matcher = None
        if self.breakpoints:
            from mas.library.standard.lib.triggers import Trigger, TriggerMatcher, When

            compiled: list[Trigger] = []
            for index, breakpoint in enumerate(self.breakpoints):
                occurrence = breakpoint.occurrence if breakpoint.occurrence is not None else "every"
                if breakpoint.first_only:
                    occurrence = "first"
                every = occurrence in {None, "every"}
                compiled.append(
                    Trigger(
                        id=f"bp-{index}",
                        when=When(
                            on=breakpoint.kind,  # type: ignore[arg-type]
                            tool=breakpoint.tool_name,
                            agent=breakpoint.agent,
                            occurrence=occurrence if occurrence != "every" else "every",
                        ),
                        repeat=every,
                        max_fires=10_000 if every else 1,
                    )
                )
            self._matcher = TriggerMatcher(tuple(compiled))

    def bind_session(self, session: Any, *, control: Any = None, manager: Any = None) -> None:
        self._session = session
        self._control = control
        self._manager = manager

    def transition_filters(self) -> list[GovTransitionFilter]:
        from mas.library.standard.lib.triggers import Trigger
        from mas.library.standard.plugins.governance.environment import _NOBODY, _filters_for

        if self._matcher is None:
            # An empty list would subscribe the plugin to every transition at the driver.
            return [_NOBODY]
        triggers: tuple[Trigger, ...] = self._matcher.triggers
        return _filters_for(triggers)

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        return (
            GovDecision.ALLOW,
            PLUGIN_ID,
            "debug_script observes; it does not block",
        )

    def on_transition(self, transition: object) -> None:
        if self._matcher is None:
            return
        if _is_tool_call(transition):
            on = "tool_call"
        elif _is_tool_result(transition):
            on = "tool_result"
        elif getattr(transition, "hook", "") == "egress" and getattr(transition, "op", "") == "LLM_CALL":
            on = "llm_call"
        elif getattr(transition, "hook", "") == "ingress" and getattr(transition, "kind", "") == "USER_INPUT_RECEIVED":
            on = "turn_start"
        elif getattr(transition, "hook", "") == "ingress" and getattr(transition, "response_kind", "") == "MODEL_TEXT":
            on = "turn_end"
        else:
            return
        name = _transition_tool_name(transition)
        agent = str(getattr(transition, "agent_id", "") or "")
        entry = ""
        if self._session is not None:
            entry = str(getattr(self._session, "entry_agent_id", "") or "")
        hits = self._matcher.match(on=on, tool=name, agent=agent, entry_agent=entry)
        for trigger in hits:
            index = int(trigger.id.split("-", 1)[1])
            breakpoint = self.breakpoints[index]
            self._emit(
                f"Breakpoint {index}: {breakpoint.kind}" + (f" {breakpoint.tool_name}" if breakpoint.tool_name else "")
            )
            self._record_breakpoint(breakpoint, index, transition)
            self._run_commands(breakpoint, transition)

    def _record_breakpoint(self, breakpoint: Breakpoint, index: int, transition: object) -> None:
        obs = getattr(getattr(getattr(self._session, "instance", None), "driver", None), "observability", None)
        record = getattr(obs, "record_control", None)
        if not callable(record):
            return
        record(
            method="breakpoint",
            category="debug.breakpoint",
            control_kind="debug_script",
            actor="debug_script",
            surface="plugin",
            session_id=str(getattr(transition, "session_id", "") or self._session_id()),
            hook=str(getattr(transition, "hook", "") or ""),
            tool_name=_transition_tool_name(transition),
            breakpoint_kind=breakpoint.kind,
            breakpoint_index=index,
        )

    def _run_commands(self, breakpoint: Breakpoint, transition: object) -> None:
        commands = breakpoint.commands or (
            "checkpoint",
            "info checkpoints",
            "info session",
            "continue",
        )
        for command in commands:
            self._run_command(command, breakpoint, transition)

    def _run_command(self, command: str, breakpoint: Breakpoint, transition: object) -> None:
        verb, _, rest = command.strip().partition(" ")
        verb = verb.lower()
        rest = rest.strip()
        if verb in {"checkpoint", "bt"}:
            self._cmd_checkpoint(breakpoint, transition)
            return
        if verb == "info":
            self._cmd_info(rest, transition)
            return
        if verb == "print":
            self._cmd_info(rest or "session", transition)
            return
        if verb == "pause":
            self._cmd_pause(transition)
            return
        if verb in {"continue", "resume"}:
            self._cmd_resume(transition)
            return
        if verb == "env":
            self._cmd_env(rest, transition)
            return
        if verb == "emit":
            self._cmd_emit(rest, transition)
            return
        raise ValueError(f"unknown breakpoint command {command!r}")

    def _cmd_env(self, name: str, transition: object) -> None:
        from mas.library.standard.lib.environment_port import get_environment_port

        port = get_environment_port()
        if port is None:
            self._emit("env skipped (no lab environment)")
            return
        run = getattr(port, "run_named_action", None)
        if not callable(run):
            self._emit("env skipped (port has no named actions)")
            return
        try:
            run(name.strip())
        except KeyError:
            self._emit(f"env skipped (unknown action {name.strip()})")
            return
        self._emit(f"env {name.strip()}")

    def _cmd_emit(self, name: str, transition: object) -> None:
        from mas.library.standard.lib.environment_port import get_environment_port

        port = get_environment_port()
        folded = name.strip()
        if not folded.startswith("user."):
            folded = f"user.{folded}"
        if port is None:
            self._emit("emit skipped (no lab environment)")
            return
        emit = getattr(port, "emit_named", None)
        if not callable(emit):
            self._emit("emit skipped (port has no emit)")
            return
        try:
            emit(folded, source={"type": "gdb", "id": "gdb"})
        except ValueError as exc:
            self._emit(f"emit skipped ({exc})")
            return
        self._emit(f"emit {folded}")

    def _cmd_checkpoint(self, breakpoint: Breakpoint, transition: object) -> None:
        session_id = str(getattr(transition, "session_id", "") or self._session_id())
        tool_name = _transition_tool_name(transition)
        reason = f"break {breakpoint.kind}" + (f" {tool_name}" if tool_name else "")
        working_memory = self._working_memory(session_id)
        checkpoint_id = ""
        if self._control is not None:
            ref = self._control.snapshot(session_id, label=reason, auto_stop=True)
            checkpoint_id = str(getattr(ref, "snapshot_id", "") or "")
        session = self._session
        if not checkpoint_id and session is not None and hasattr(session, "take_snapshot"):
            snap = session.take_snapshot(
                label=reason,
                kind="debug",
                hook=str(getattr(transition, "hook", "") or ""),
                op=str(getattr(transition, "op", "") or getattr(transition, "response_kind", "") or ""),
                correlation_id=int(getattr(transition, "correlation_id", 0) or 0),
            )
            checkpoint_id = str(getattr(getattr(snap, "ref", snap), "snapshot_id", "") or "")
        if not checkpoint_id:
            checkpoint_id = str(uuid.uuid4())
        record = MemoryCheckpoint(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            reason=reason,
            hook=str(getattr(transition, "hook", "") or ""),
            tool_name=tool_name,
            q_state=dict(getattr(transition, "q_state", None) or {}),
            working_memory=working_memory,
            attributes=dict(getattr(transition, "attributes", None) or {}),
        )
        self.checkpoints.append(record)
        self._emit(f"checkpoint {checkpoint_id} session {session_id}")

    def _cmd_info(self, topic: str, transition: object) -> None:
        key = (topic or "session").lower().replace("-", "_")
        session_id = str(getattr(transition, "session_id", "") or self._session_id())
        if key in {"session", "sessions"}:
            self._emit(f"session {session_id}")
            if self.checkpoints:
                self._emit(f"last_checkpoint {self.checkpoints[-1].checkpoint_id}")
            return
        if key in {"checkpoint", "checkpoints"}:
            self._emit(f"checkpoints {len(self.checkpoints)}")
            for item in self.checkpoints:
                self._emit(
                    f"  {item.checkpoint_id} session={item.session_id} {item.reason} tool={item.tool_name or '-'}"
                )
            if self._control is not None:
                nodes = self._control.list_checkpoints(session_id)
                self._emit(f"snapshot_tree {len(nodes)}")
                for node in nodes:
                    self._emit(f"  tree {getattr(node, 'snapshot_id', node)} label={getattr(node, 'label', '')}")
            return
        if key in {"working_memory", "wm", "state"}:
            self._emit(f"working_memory session {session_id}")
            for entry in self._working_memory(session_id):
                agent_id = entry.get("agent_id", "")
                history = entry.get("turn_history") or []
                messages = entry.get("committed_messages") or entry.get("working_messages") or []
                self._emit(f"  agent {agent_id} turns={len(history)} messages={len(messages)}")
                for turn in history:
                    self._emit(f"    history {turn!r}")
                for message in messages:
                    role = message.get("role") if isinstance(message, dict) else ""
                    content = message.get("content") if isinstance(message, dict) else message
                    preview = str(content)[:240]
                    self._emit(f"    {role}: {preview}")
            q_state = getattr(transition, "q_state", None) or {}
            if q_state:
                self._emit(f"  q_state {q_state}")
            return
        raise ValueError(f"unknown info topic {topic!r}")

    def _cmd_pause(self, transition: object) -> None:
        session_id = str(getattr(transition, "session_id", "") or self._session_id())
        if self._control is None:
            self._emit(f"pause skipped (no control) session {session_id}")
            return
        self._control.pause(session_id, reason="debug_script breakpoint")
        self._emit(f"paused {session_id}")

    def _cmd_resume(self, transition: object) -> None:
        session_id = str(getattr(transition, "session_id", "") or self._session_id())
        if self._control is None:
            self._emit(f"continue session {session_id}")
            return
        session = None
        if self._manager is not None:
            try:
                session = self._manager.get(session_id)
            except KeyError:
                session = None
        if getattr(session, "status", None) == SessionStatus.PAUSED:
            self._control.resume(session_id)
            self._emit(f"resumed {session_id}")
            return
        self._emit(f"continue session {session_id}")

    def _session_id(self) -> str:
        return str(getattr(self._session, "session_id", "") or "")

    def _working_memory(self, session_id: str) -> list[dict[str, Any]]:
        registry = getattr(self._session, "working_memory", None)
        if registry is None:
            from mas.runtime.boundary.context.working_memory_registry import (
                get_working_memory_registry,
            )

            registry = get_working_memory_registry()
        export = getattr(registry, "export_session", None)
        if callable(export):
            return list(export(session_id) or [])
        return []

    def _emit(self, line: str) -> None:
        self.log.append(line)
        print(f"[debug_script] {line}", file=self.out, flush=True)
