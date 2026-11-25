#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MCE DataProvider backed by a mas-lab events.jsonl trace file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class MASTraceProvider:
    """MCE ``DataProvider`` that reads a JSONL trace produced by EventRecorder.

    Extracts two required context keys:

    * ``input_query``   — task prompt from the root ``execution_start`` event
                          (or a legacy ``audit`` event when present)
    * ``final_response``— last response-bearing event from *response_agent_id*
                          (``execution_end.output``, ``user_response``, …;
                          auto-detected from the trace when not supplied)

    Optional:

    * ``ground_truth``  — injected at construction from a fixture YAML
    * ``conversation_text`` — concatenated tool call + LLM exchange for
                              Groundedness-style metrics

    Parameters
    ----------
    ground_truth:
        Optional reference answer to inject into the MCE context.
    response_agent_id:
        agent_id whose last ``execution_end`` is taken as the final response.
        When ``None`` (default), the root agent is auto-detected from the
        trace: the agent_id found in the root ``execution_start`` event
        (``parent_call_id is None``).  This makes the provider usable across
        any MAS topology without configuration.
    """

    def __init__(
        self,
        ground_truth: Optional[str] = None,
        response_agent_id: Optional[str] = None,
    ) -> None:
        self._ground_truth = ground_truth
        self._response_agent_id = response_agent_id

    # ------------------------------------------------------------------
    # DataProvider protocol
    # ------------------------------------------------------------------

    def fetch(self, resource_id: str, requirements: Any) -> Dict[str, Any]:
        """Read *resource_id* (path to ``events.jsonl``) and build context.

        ``requirements`` is accepted but ignored — the full context is
        always returned so any MCE metric can pick what it needs.
        """
        trace_path = Path(resource_id)
        if not trace_path.exists():
            raise FileNotFoundError(f"Trace file not found: {trace_path}")

        events: list[dict] = []
        with trace_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        input_query = self._extract_query(events)
        response_agent = self._response_agent_id or self._detect_root_agent(events)
        final_response = self._extract_response(events, response_agent)
        conversation_text = self._build_conversation(events)
        tool_spans = self._extract_tool_spans(events)
        session_node = self._build_session_node(events)

        ctx: Dict[str, Any] = {
            "input_query": input_query,
            "final_response": final_response,
            "conversation_text": conversation_text,
            # Aliases expected by some MCE utilities
            "input_text": input_query,
            "output_text": final_response,
            # Groundedness variants
            "conversation_data": conversation_text,
            "transcript": conversation_text,
            # Tool spans for ToolUtilizationAccuracy (session-avg mode)
            "tool_spans": tool_spans,
            # Session node for MCE Duration (session_id injected by runner)
            "session": session_node,
            # Raw events for custom metrics
            "events": events,
            "trace_path": str(trace_path),
        }
        if self._ground_truth is not None:
            ctx["ground_truth"] = self._ground_truth

        return ctx

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_root_agent(self, events: list[dict]) -> Optional[str]:
        """Return the agent_id from the root ``execution_start`` event.

        Handles two formats:
        - Pre-mas_call_start: root event has ``parent_call_id is None``.
        - Current format: root event's ``parent_call_id`` is the ``call_id``
          of a ``mas_call_start`` event (set by ``ObsPluginSet.begin_run``).
        Falls back to the agent_id of the last ``execution_end`` event in the trace.
        """
        # Primary — old format: no parent
        for e in events:
            if e.get("kind") == "execution_start" and e.get("parent_call_id") is None:
                agent = e.get("agent_id")
                if agent:
                    return str(agent)
        # Primary — current format: parented to mas_call_start
        mas_call_ids: set = {e.get("call_id") for e in events if e.get("kind") == "mas_call_start" and e.get("call_id")}
        if mas_call_ids:
            for e in events:
                if e.get("kind") == "execution_start" and e.get("parent_call_id") in mas_call_ids:
                    agent = e.get("agent_id")
                    if agent:
                        return str(agent)
        # Fallback: last execution_end
        last_agent: Optional[str] = None
        for e in events:
            if e.get("kind") == "execution_end":
                agent = e.get("agent_id")
                if agent:
                    last_agent = str(agent)
        return last_agent

    def _extract_query(self, events: list[dict]) -> str:
        """Return the task prompt.

        Resolution order:
        1. Legacy ``kind == "audit"`` event (``payload.task.prompt`` or ``payload.prompt``).
        2. Root ``kind == "execution_start"`` event (``parent_call_id is None``)
           with a non-empty ``input`` field (pre-mas_call_start format).
        3. Entry-agent ``kind == "execution_start"`` whose ``parent_call_id`` is the
           ``call_id`` of a ``mas_call_start`` event (current format — ``begin_run``
           wraps all runs in a ``mas_call_start`` frame, so no execution_start is
           truly root any more).
        """
        # Pass 1 — legacy audit event
        for e in events:
            if e.get("kind") == "audit" or e.get("type") == "audit":
                prompt = (
                    e.get("payload", {}).get("task", {}).get("prompt")
                    or e.get("payload", {}).get("prompt")
                    or e.get("prompt")
                )
                if prompt:
                    return str(prompt)

        # Pass 2 — root execution_start (pre-mas_call_start format)
        for e in events:
            if e.get("kind") == "execution_start" and e.get("parent_call_id") is None:
                inp = e.get("input")
                if inp:
                    return str(inp)

        # Pass 3 — entry-agent execution_start parented to mas_call_start (current format)
        mas_call_ids: set = {e.get("call_id") for e in events if e.get("kind") == "mas_call_start" and e.get("call_id")}
        if mas_call_ids:
            for e in events:
                if e.get("kind") == "execution_start" and e.get("parent_call_id") in mas_call_ids:
                    inp = e.get("input")
                    if inp:
                        return str(inp)

        return ""

    def _extract_response(self, events: list[dict], response_agent: Optional[str]) -> str:
        """Return the best final response candidate for *response_agent*.

        Primary source is still ``execution_end.output``. Some traces can end
        without that field while still containing a model response (for example
        in ``llm_call_end.response.content``). This method scans a short list of
        response-bearing events and keeps the last non-empty candidate.
        """

        def _as_text(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, (int, float, bool)):
                return str(value)
            if isinstance(value, dict):
                # Common OpenAI/LLM payload shapes.
                for key in ("content", "output_text", "text", "output", "response", "message", "result"):
                    nested = _as_text(value.get(key))
                    if nested:
                        return nested
                return ""
            if isinstance(value, list):
                for item in value:
                    nested = _as_text(item)
                    if nested:
                        return nested
                return ""
            return str(value).strip()

        def _candidate_from_event(event: dict) -> str:
            kind = event.get("kind")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

            # Prioritize top-level output for terminal events.
            if kind == "execution_end":
                return _as_text(event.get("output") or payload.get("output"))

            # user_output / user_response carry rendered assistant text.
            if kind in ("user_output", "user_response"):
                return _as_text(
                    event.get("output")
                    or event.get("content")
                    or event.get("response")
                    or payload.get("output")
                    or payload.get("content")
                    or payload.get("response")
                )

            # Some runs miss execution_end.output but have llm_call_end.response.content.
            if kind == "llm_call_end":
                return _as_text(
                    event.get("response") or payload.get("response") or event.get("output") or payload.get("output")
                )

            return ""

        response = ""
        fallback = ""
        for e in events:
            candidate = _candidate_from_event(e)
            if not candidate:
                continue
            fallback = candidate
            if response_agent is None or e.get("agent_id") == response_agent:
                response = candidate

        return response or fallback

    def _build_session_node(self, events: list[dict]) -> dict:
        """Build a session node dict compatible with the MCE Duration metric.

        Returns a dict with ``durationMs``, ``startTime`` and ``endTime``
        (all in milliseconds) derived from root execution timestamps.
        The caller (runner) must inject ``session_id`` into the context so
        :class:`~mce.providers.native.metrics.session_metrics.Duration` can
        detect the resource type correctly.
        """
        root_start: float | None = None
        root_agent: str | None = None
        # Collect mas_call_start IDs for current-format detection.
        _mas_call_ids: set = {
            e.get("call_id") for e in events if e.get("kind") == "mas_call_start" and e.get("call_id")
        }
        for e in events:
            parent = e.get("parent_call_id")
            is_root = parent is None or parent in _mas_call_ids
            if e.get("kind") == "execution_start" and is_root:
                root_start = e.get("timestamp")
                root_agent = e.get("agent_id")
                break

        root_end: float | None = None
        if root_agent is not None:
            for e in events:
                if e.get("kind") == "execution_end" and e.get("agent_id") == root_agent:
                    ts = e.get("timestamp")
                    if ts is not None:
                        root_end = float(ts)

        if root_start is not None and root_end is not None:
            # Convert seconds → milliseconds (MCE Duration unit)
            start_ms = root_start * 1000.0
            end_ms = root_end * 1000.0
            return {
                "startTime": start_ms,
                "endTime": end_ms,
                "durationMs": round(end_ms - start_ms, 3),
            }
        return {}

    def _extract_tool_spans(self, events: list[dict]) -> list[dict]:
        """Build tool span dicts with MCE-expected field names.

        Merges ``tool_call_start`` + ``tool_call_end`` pairs by ``call_id``.
        Returns dicts with ``toolName``, ``toolArguments``, ``toolOutput``,
        ``executionId`` — the names expected by :class:`ToolUtilizationAccuracy`.
        """
        starts = {e["call_id"]: e for e in events if e.get("kind") == "tool_call_start" and "call_id" in e}
        ends = {e["call_id"]: e for e in events if e.get("kind") == "tool_call_end" and "call_id" in e}
        spans: list[dict] = []
        for call_id, start in starts.items():
            end = ends.get(call_id, {})
            args = start.get("arguments")
            result = end.get("result")
            spans.append(
                {
                    "executionId": call_id,
                    "toolName": start.get("tool_name") or "",
                    "toolArguments": json.dumps(args)
                    if isinstance(args, (dict, list))
                    else (str(args) if args else ""),
                    "toolOutput": json.dumps(result)
                    if isinstance(result, (dict, list))
                    else (str(result) if result else ""),
                }
            )
        return spans

    def _build_conversation(self, events: list[dict]) -> str:
        """Build a flat conversation transcript from tool + LLM events."""
        lines: list[str] = []
        for e in events:
            kind = e.get("kind") or e.get("type") or ""
            agent = e.get("agent_id", "?")
            match kind:
                case "tool_call_start":
                    tool = e.get("tool_name") or e.get("payload", {}).get("tool_name", "?")
                    args = e.get("arguments") or e.get("payload", {}).get("arguments", {})
                    lines.append(f"[{agent}] TOOL_CALL {tool}({args})")
                case "tool_call_end":
                    result = e.get("result") or e.get("payload", {}).get("result", "")
                    lines.append(f"[{agent}] TOOL_RESULT {str(result)[:300]}")
                case "execution_end":
                    output = e.get("output") or e.get("payload", {}).get("output", "")
                    lines.append(f"[{agent}] RESPONSE {str(output)[:500]}")
                case "user_response":
                    content = e.get("content") or e.get("payload", {}).get("content", "")
                    from mas.runtime.contracts.content_types import text_of

                    lines.append(f"[{agent}] RESPONSE {text_of(content, media='placeholder')[:500]}")
        return "\n".join(lines)
