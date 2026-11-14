#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pretty-print helpers for LLM / tool exchange snapshots.

Call these at a display edge (CLI --trace full, cache-key materialization,
tests). Do not store their output on ExchangeRecord.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ExchangeSnapshot:
    """Structured outbound engine state for one invoke. Not a display string."""

    messages: list[dict[str, Any]] | None = None
    tools: list[dict[str, Any]] | None = None
    tools_note: str = ""
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    note: str = ""


def tool_def_names(tools: list[Any] | None) -> list[str]:
    """Names of the tools offered to the model, in order.

    Accepts OpenAI tool definitions (``{"function": {"name": ...}}``), flat
    ``{"name": ...}`` dicts, and plain strings. Entries without a name are
    skipped. Shared by the live engine (``EngineIoReturn.offered_tools``) and
    the driver (``llm_call_start.tools``) so both report the same list.
    """
    names: list[str] = []
    for tool in tools or []:
        if isinstance(tool, str):
            name = tool
        elif isinstance(tool, dict):
            fn = tool.get("function")
            name = (fn.get("name") if isinstance(fn, dict) else None) or tool.get("name")
        else:
            continue
        if name:
            names.append(str(name))
    return names


def format_exchange_snapshot(snap: ExchangeSnapshot) -> str:
    """Pretty-print an ExchangeSnapshot. Last-step view, not interchange."""
    if snap.messages:
        return format_llm_messages(snap.messages, tools=snap.tools, tools_note=snap.tools_note)
    if snap.tool_name:
        return format_tool_invoke(snap.tool_name, snap.arguments)
    return snap.note


def format_llm_messages(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tools_note: str = "",
) -> str:
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "?")
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content")
        if role == "tool":
            call_id = msg.get("tool_call_id") or "?"
            text = str(content or "").strip()
            lines.append(f"[tool call_id={call_id}]")
            for part in (text.splitlines() or [text]) if text else ["(empty)"]:
                lines.append(f"  {part}")
            continue
        if tool_calls:
            lines.append("[assistant tool_calls]")
            for call in tool_calls:
                fn = call.get("function") or {}
                lines.append(f"  id={call.get('id')} name={fn.get('name')}")
            continue
        if isinstance(content, list):
            text = json.dumps(content, ensure_ascii=False)
        else:
            text = str(content or "").strip()
        if not text:
            continue
        if role == "system":
            lines.append("[system]  (single API message; section tags below are text, not roles)")
        else:
            lines.append(f"[{role}]")
        for part in text.splitlines() or [text]:
            lines.append(f"  {part}")
    if tools:
        lines.append("[tools]  (included in API payload)")
        for tool in tools:
            fn = (tool.get("function") or {}) if isinstance(tool, dict) else {}
            name = fn.get("name") or tool.get("name") or "?"
            lines.append(f"  - {name}")
    elif tools_note:
        lines.append(f"[tools]  {tools_note}")
    return "\n".join(lines)


def format_tool_invoke(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    args = arguments or {}
    try:
        args_text = json.dumps(args, ensure_ascii=False, indent=2)
    except TypeError:
        args_text = str(args)
    return f"tool={tool_name}\nargs={args_text}"


def format_llm_response(
    *,
    text: str = "",
    next_step: str = "",
    tool_name: str = "",
    tool_arguments: dict[str, Any] | None = None,
    response_kind: str = "MODEL_TEXT",
) -> str:
    lines: list[str] = []
    if response_kind == "ERROR":
        lines.append(f"ERROR: {text}")
        return "\n".join(lines)
    if text.strip():
        lines.append("content:")
        for line in text.splitlines() or [text]:
            lines.append(f"  {line}")
    if next_step == "TOOL_CALL" and tool_name:
        lines.append("tool_call:")
        lines.append(f"  name: {tool_name}")
        args = tool_arguments or {}
        try:
            args_text = json.dumps(args, ensure_ascii=False, indent=2)
        except TypeError:
            args_text = str(args)
        for line in args_text.splitlines():
            lines.append(f"  {line}")
    elif next_step and next_step not in {"STOP", "MODEL_TEXT"}:
        lines.append(f"next_step: {next_step}")
    return "\n".join(lines)
