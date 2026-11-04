#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TrajectoryExtractor — parse one MAS run trace into a flat trajectory record.

One ``events.jsonl`` → one dict suitable for appending to ``trajectories.jsonl``.

Schema of the output dict::

    {
      "run_id":        str,
      "scenario":      str,
      "item_id":       str | int,
      "run":           int,
      "group":         str,
      "target_agents": list[str],
      "prompt":        str,
      "session": {
        "input":  str,
        "output": str,
      },
      "agents": {
        "<agent_id>": {
          "first_user_message": str,
          "final_output":       str,
          "n_calls":            int,
          "tool_names":         list[str],
        },
        ...
      },
      "calls": [                       # one record per LLM call (ordered)
        {
          "agent_id":       str,
          "call_idx":       int,       # 0-based index within that agent
          "model":          str,
          "input_prompt":   str,       # last non-system message text
          "input_messages": list[dict],# full messages array (role + content)
          "output":         str,       # LLM response content
          "latency_ms":     float | None,
          "tool_calls":     list[str], # tool names called after this LLM step
        },
        ...
      ],
      "n_total_llm_calls": int,
      "n_total_tool_calls": int,
    }
      "n_total_tool_calls": int,
    }

Tool-call details live in the raw trace; this record captures the
session-level and per-agent I/O summaries needed for embeddings and metrics.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TrajectoryExtractor:
    """Extract a trajectory record from a single run's ``events.jsonl``.

    Args:
        trace_path: Path to ``traces/events.jsonl``.
        run_meta:   Provenance dict (run_id, scenario, item_id, run, group,
                    target_agents, prompt).  Merged verbatim into the output.
    """

    def __init__(
        self,
        trace_path: "str | Path",
        run_meta: dict[str, Any] | None = None,
    ) -> None:
        self.trace_path = Path(trace_path)
        self.run_meta = run_meta or {}

    def extract(self) -> dict[str, Any]:
        """Return a flat trajectory record dict."""
        if not self.trace_path.exists():
            logger.warning("Trace not found: %s", self.trace_path)
            return {
                **self.run_meta,
                "session": {"input": "", "output": ""},
                "agents": {},
                "n_total_llm_calls": 0,
                "n_total_tool_calls": 0,
                "_extraction_error": "trace_not_found",
            }

        events = _load_events(self.trace_path)

        # --- session level ---
        session_input = _find_session_input(events)
        session_output = _find_session_output(events)

        # --- per-agent: lifecycle events + llm calls ---
        agent_calls: dict[str, list[dict]] = defaultdict(list)
        agent_tool_names: dict[str, list[str]] = defaultdict(list)
        # Authoritative per-agent I/O from execution lifecycle events.
        agent_exec_input: dict[str, str] = {}   # execution_start.input
        agent_exec_output: dict[str, str] = {}  # execution_end.output
        pending_msgs: dict[str, list] = {}  # agent_id -> messages from last llm_call_start

        for ev in events:
            kind = ev.get("kind", "")
            agent_id = ev.get("agent_id", "")

            if kind == "execution_start":
                inp = ev.get("input", "")
                if inp and agent_id:
                    agent_exec_input[agent_id] = inp

            elif kind == "execution_end":
                out = ev.get("output", "")
                if out and agent_id:
                    agent_exec_output[agent_id] = out

            elif kind == "llm_call_start":
                pending_msgs[agent_id] = ev.get("messages") or []

            elif kind == "llm_call_end":
                msgs = pending_msgs.pop(agent_id, [])
                response = ev.get("response", {})
                content = (response.get("content", "") if isinstance(response, dict)
                           else str(response))
                agent_calls[agent_id].append({
                    "input_messages": msgs,
                    "output": content,
                })

            elif kind == "tool_call_start" and agent_id:
                agent_tool_names[agent_id].append(ev.get("tool_name", ""))

        # Merge all seen agents (execution lifecycle + llm calls).
        all_agent_ids = set(agent_exec_input) | set(agent_exec_output) | set(agent_calls)
        agents: dict[str, Any] = {}
        for agent_id in all_agent_ids:
            calls = agent_calls.get(agent_id, [])
            # Prefer execution lifecycle events; fall back to llm_call data.
            first_msg = (
                agent_exec_input.get(agent_id)
                or _first_user_message(calls[0]["input_messages"] if calls else [])
            )
            final_out = (
                agent_exec_output.get(agent_id)
                or (calls[-1]["output"] if calls else "")
            )
            agents[agent_id] = {
                "first_user_message": first_msg,
                "final_output": final_out,
                "n_calls": len(calls),
                "tool_names": agent_tool_names.get(agent_id, []),
            }

        n_tool_calls = sum(1 for ev in events if ev.get("kind") == "tool_call_start")

        # --- call level: one record per llm_call_start/end pair ---
        # Captures the full prompt→LLM→tool flow for each reasoning step.
        calls: list[dict[str, Any]] = []
        _pending_call: dict[str, dict] = {}  # agent_id -> open llm_call_start info

        for ev in events:
            kind = ev.get("kind", "")
            agent_id = ev.get("agent_id", "")

            if kind == "llm_call_start":
                msgs = ev.get("messages") or []
                _pending_call[agent_id] = {
                    "agent_id": agent_id,
                    "call_idx": len([c for c in calls if c["agent_id"] == agent_id]),
                    "model": ev.get("model", ""),
                    "input_messages": msgs,
                    # Last non-system message as a convenience text field
                    "input_prompt": next(
                        (m.get("content", "") for m in reversed(msgs)
                         if m.get("role") != "system"),
                        ""
                    ),
                    "latency_ms": None,
                    "output": "",
                    "tool_calls": [],
                }

            elif kind == "llm_call_end" and agent_id in _pending_call:
                rec = _pending_call.pop(agent_id)
                response = ev.get("response", {})
                rec["output"] = (
                    response.get("content", "") if isinstance(response, dict)
                    else str(response)
                )
                rec["latency_ms"] = ev.get("latency_ms")
                calls.append(rec)

            elif kind == "tool_call_start" and calls:
                # Attach to the last completed call for this agent
                for rec in reversed(calls):
                    if rec["agent_id"] == agent_id:
                        rec["tool_calls"].append(ev.get("tool_name", ""))
                        break

        # Fall back to the prompt from run_meta when the trace doesn't carry
        # the user message (e.g. mock runs where messages=None).
        if not session_input:
            session_input = self.run_meta.get("prompt", "")

        # Backfill agent-level first_user_message from session input when missing.
        # This ensures agent.first_user_message == session.input for the entry-point agent.
        for agent_data in agents.values():
            if not agent_data["first_user_message"]:
                agent_data["first_user_message"] = session_input

        return {
            **self.run_meta,
            "session": {"input": session_input, "output": session_output},
            "agents": agents,
            "calls": calls,
            "n_total_llm_calls": sum(len(c) for c in agent_calls.values()),
            "n_total_tool_calls": n_tool_calls,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def _find_session_input(events: list[dict]) -> str:
    """First user-role content encountered in the trace.

    Checks ``llm_call_start.messages`` first; falls back to the entry agent's
    ``execution_start.input`` (the task prompt passed to ``handle_task``).
    """
    for ev in events:
        if ev.get("kind") == "llm_call_start":
            for msg in (ev.get("messages") or []):
                if msg.get("role") == "user":
                    return _msg_content(msg)
    # Fallback: execution_start carries input_prompt for every agent.
    # Use the first execution_start seen in the trace (entry agent fires first).
    for ev in events:
        if ev.get("kind") == "execution_start":
            inp = ev.get("input", "")
            if inp:
                return inp
    return ""


def _find_session_output(events: list[dict]) -> str:
    """Content of the last ``user_response`` event (final MAS answer).

    Falls back to the entry agent's ``execution_end.output`` when no
    ``user_response`` event is present (e.g. single-agent runs without
    explicit user output emission).
    """
    output = ""
    for ev in events:
        if ev.get("kind") == "user_response":
            content = ev.get("content", "")
            if isinstance(content, dict):
                content = content.get("content", str(content))
            if content:
                output = str(content)
    if output:
        return output
    # Fallback: entry agent has is_entry_agent=True in execution_end context.
    for ev in reversed(events):
        if ev.get("kind") == "execution_end":
            ctx = ev.get("context", {})
            if ctx.get("is_entry_agent"):
                out = ev.get("output", "")
                if out:
                    return out
    return output


def _first_user_message(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            return _msg_content(msg)
    return ""


def _msg_content(msg: dict) -> str:
    content = msg.get("content", "")
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and "text" in p
        )
    return str(content)
