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

BreakpointKind = Literal["tool_call", "tool_result"]


@dataclass(frozen=True)
class Breakpoint:
    kind: BreakpointKind
    tool_name: str = ""
    first_only: bool = False
    commands: tuple[str, ...] = ()


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
        raise ValueError("break requires tool_call or tool_result")
    kind_token = rest[0].lower().replace("-", "_")
    if kind_token not in {"tool_call", "tool_result"}:
        raise ValueError(f"break target must be tool_call or tool_result, got {rest[0]!r}")
    tool_name = ""
    first_only = False
    idx = 1
    if idx < len(rest) and rest[idx].lower() not in {"if"}:
        tool_name = rest[idx]
        idx += 1
    if idx < len(rest):
        if rest[idx].lower() != "if" or idx + 1 >= len(rest) or rest[idx + 1].lower() != "first":
            raise ValueError(f"unsupported break condition {' '.join(rest[idx:])!r}")
        first_only = True
        if kind_token != "tool_result":
            raise ValueError("if first is only valid on break tool_result")
    return Breakpoint(kind=kind_token, tool_name=tool_name, first_only=first_only)


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
        self._hits: dict[int, int] = {}
        self._session: Any = None
        self._control: Any = None
        self._manager: Any = None

    def bind_session(self, session: Any, *, control: Any = None, manager: Any = None) -> None:
        self._session = session
        self._control = control
        self._manager = manager

    def transition_filters(self) -> list[GovTransitionFilter]:
        return [
            GovTransitionFilter(hook="egress", op=("TOOL_CALL",)),
            GovTransitionFilter(hook="ingress", response_kind=("TOOL_RESULT",)),
        ]

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        return (
            GovDecision.ALLOW,
            PLUGIN_ID,
            "debug_script observes; it does not block",
        )

    def on_transition(self, transition: object) -> None:
        for index, breakpoint in enumerate(self.breakpoints):
            if self._matches(breakpoint, index, transition):
                self._emit(
                    f"Breakpoint {index}: {breakpoint.kind}"
                    + (f" {breakpoint.tool_name}" if breakpoint.tool_name else "")
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

    def _matches(self, breakpoint: Breakpoint, index: int, transition: object) -> bool:
        if breakpoint.kind == "tool_call" and not _is_tool_call(transition):
            return False
        if breakpoint.kind == "tool_result" and not _is_tool_result(transition):
            return False
        name = _transition_tool_name(transition)
        if breakpoint.tool_name and name != breakpoint.tool_name:
            return False
        self._hits[index] = self._hits.get(index, 0) + 1
        if breakpoint.first_only and self._hits[index] != 1:
            return False
        return True

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
        raise ValueError(f"unknown breakpoint command {command!r}")

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
