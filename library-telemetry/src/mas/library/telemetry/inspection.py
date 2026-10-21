#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Read a converted span file the way an on-call engineer would.

Three questions, three functions: did anything fail (:func:`error_report`),
what was said and called, in order (:func:`conversation`), and which calls to a
named tool were made, with their inputs (:func:`tool_calls`). Nothing here
knows an agent or a tool name: the caller supplies the tool names to look for.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from mas.library.telemetry.verification.spanspec import get_attrs

_ERROR_CODES = {"ERROR", "STATUS_CODE_ERROR"}
_COMPLETION_KEYS = ("gen_ai.completion.0.content", "mas.llm.response", "ioa_observe.entity.output")


@dataclass(frozen=True)
class Turn:
    """One line of the conversation: an LLM reply or a tool call."""

    index: int
    agent: str
    kind: str  # "LLM" or "TOOL"
    text: str


def status_code(span: dict[str, Any]) -> str:
    status = span.get("status") or {}
    code = status.get("status_code") if isinstance(status, dict) else status
    return str(code or "UNSET")


def agent_of(span: dict[str, Any]) -> str:
    attrs = get_attrs(span)
    return str(attrs.get("mas.agent.id") or attrs.get("agent_id") or "")


def error_report(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Span count, status counts and the number of ERROR spans."""
    counts = Counter(status_code(s) for s in spans)
    errors = sum(n for code, n in counts.items() if code.upper() in _ERROR_CODES)
    return {"spans": len(spans), "status_counts": dict(counts), "error_spans": errors}


def _completion(span: dict[str, Any]) -> str:
    attrs = get_attrs(span)
    for key in _COMPLETION_KEYS:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip() and not value.startswith("llm-output:"):
            return value
    return ""


def _tool_input(span: dict[str, Any]) -> str:
    attrs = get_attrs(span)
    return str(attrs.get("mas.tool.input") or attrs.get("mas.input") or "")


def conversation(spans: list[dict[str, Any]], *, with_tools: bool = True) -> list[Turn]:
    """LLM replies (and, unless ``with_tools`` is false, tool calls) in span order."""
    turns: list[Turn] = []
    for index, span in enumerate(spans):
        name = str(span.get("name") or "")
        who = agent_of(span) or "-"
        if name.endswith(".chat"):
            text = _completion(span)
            if text:
                turns.append(Turn(index, who, "LLM", text))
        elif with_tools and name.endswith(".tool"):
            tool = str(get_attrs(span).get("mas.tool.name") or name.removesuffix(".tool"))
            turns.append(Turn(index, who, "TOOL", f"{tool}  {_tool_input(span)}"))
    return turns


def tool_calls(spans: list[dict[str, Any]], tools: list[str]) -> list[dict[str, Any]]:
    """Calls to any of ``tools``: span index, agent, tool and input, in span order."""
    wanted = set(tools)
    found = []
    for index, span in enumerate(spans):
        tool = str(get_attrs(span).get("mas.tool.name") or "")
        if tool in wanted:
            found.append({"index": index, "agent": agent_of(span) or "-", "tool": tool, "input": _tool_input(span)})
    return found
