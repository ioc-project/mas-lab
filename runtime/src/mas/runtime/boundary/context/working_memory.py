#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Chat messages for the current user turn (``source_type=working_memory``).

This list is the in-flight part of the next model prompt. The assembler
later slices it (``bounded_working_memory_tail``).

Copy rows from the kernel log. Do not search the whole session log: the
log keeps every tool result, and this store is emptied at each turn.
Searching after a reset pastes an old result into the new turn.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

SOURCE_TYPE = "working_memory"


def _tool_call_entry(call_id: str, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(dict(arguments or {})),
        },
    }


def next_unbound_tool_call_id(
    messages: Sequence[dict[str, Any]],
    extra_messages: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Id of the next assistant tool_call that has no matching tool result yet.

    HITL can dispatch parallel tools one-at-a-time, so tool-result correlation
    ids do not match the ids recorded on the assistant message. Bind in order.
    ``extra_messages`` is committed history: HITL pause folds WM there before
    the tool result arrives.
    """
    combined = list(extra_messages or []) + list(messages)
    declared: list[str] = []
    for msg in reversed(combined):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            declared = [str(c.get("id") or "") for c in (msg.get("tool_calls") or []) if c.get("id")]
            break
    bound = {str(m.get("tool_call_id") or "") for m in combined if m.get("role") == "tool"}
    for tid in declared:
        if tid not in bound:
            return tid
    return ""


@dataclass
class WorkingMemoryStore:
    messages: list[dict[str, Any]] = field(default_factory=list)
    _open_tool_call_id: str = field(default="", repr=False)
    _synced_tool_result_cids: set[int] = field(default_factory=set, repr=False)

    def clear(self) -> None:
        self.messages.clear()
        self._open_tool_call_id = ""
        self._synced_tool_result_cids.clear()

    def is_synced(self, cid: int) -> bool:
        return bool(cid) and cid in self._synced_tool_result_cids

    def mark_synced(self, cid: int) -> None:
        if cid:
            self._synced_tool_result_cids.add(cid)

    def project_new(
        self,
        rows: Sequence[Any] | None,
        extra_messages: Sequence[dict[str, Any]] | None = None,
        ctx: Any = None,
    ) -> list[tuple[Any, str]]:
        """Copy this step's new log rows. Returns ``(row, call_id)`` for writes."""
        written: list[tuple[Any, str]] = []
        for row in rows or []:
            call_id = self.project_event(row, extra_messages, ctx=ctx)
            if call_id is not None:
                written.append((row, call_id))
        return written

    def project_event(
        self,
        event: Any,
        extra_messages: Sequence[dict[str, Any]] | None = None,
        ctx: Any = None,
    ) -> str | None:
        """Copy one accepted log row into the transcript. Returns a call id if any.

        Pause, retry, and hard halt never reach here: they do not append a row.
        Empty tool-result text is valid (governance skip).
        """
        kind = str(getattr(event, "response_kind", "") or "")
        next_step = str(getattr(event, "next_step", "") or "")
        cid = int(getattr(event, "correlation_id", 0) or 0)
        text = str(getattr(event, "text", "") or "")
        finish = str(getattr(event, "finish_reason", "") or "")
        if kind in {"TOOL_RESULT", "ERROR"}:
            bound = self.project_tool_result(
                cid=cid, content=text, extra_messages=extra_messages, ctx=ctx
            )
            if bound is not None:
                return bound
            if kind == "ERROR" and text.strip():
                self.record_assistant_message(text)
                return ""
            return None
        if kind != "MODEL_TEXT" or finish == "replaced":
            return None
        if next_step == "PARALLEL_TOOL_CALLS":
            parallel = tuple(getattr(event, "parallel_tools", ()) or ())
            calls = [(f"call_{cid}_{idx}", str(name), dict(args or {})) for idx, (name, args) in enumerate(parallel)]
            if not calls:
                return None
            if self._assistant_has_call(calls[0][0], extra_messages):
                return None
            self.record_assistant_tool_calls(calls)
            return calls[0][0]
        if next_step == "TOOL_CALL":
            call_id = f"call_{cid}"
            if self._assistant_has_call(call_id, extra_messages):
                return None
            self.record_assistant_tool_call(
                call_id=call_id,
                tool_name=str(getattr(event, "tool_name", "") or "tool"),
                arguments=dict(getattr(event, "tool_arguments", None) or {}),
            )
            return call_id
        if text.strip() and next_step in {"STOP", "LLM_CALL"}:
            self.record_assistant_message(text)
            return ""
        return None

    def _assistant_has_call(
        self,
        call_id: str,
        extra_messages: Sequence[dict[str, Any]] | None = None,
    ) -> bool:
        for msg in list(extra_messages or []) + self.messages:
            if msg.get("role") != "assistant":
                continue
            for spec in msg.get("tool_calls") or []:
                if str(spec.get("id") or "") == call_id:
                    return True
        return False

    def project_tool_result(
        self,
        *,
        cid: int,
        content: str,
        extra_messages: Sequence[dict[str, Any]] | None = None,
        ctx: Any = None,
    ) -> str | None:
        """Bind one committed TOOL_RESULT. Returns the tool_call_id written.

        ``None`` means the row was already projected, or there is no open
        assistant tool_call to bind. Empty ``content`` is valid (governance SKIP).
        """
        if self.is_synced(cid):
            return None
        call_id = next_unbound_tool_call_id(self.messages, extra_messages) or self._open_tool_call_id
        if not call_id:
            return None
        if self.messages and self.messages[-1].get("role") == "tool":
            if self.messages[-1].get("tool_call_id", "") == call_id:
                self.mark_synced(cid)
                return None
        chunk = None
        if ctx is not None:
            from mas.runtime.boundary.context.chunk_meta import take_pending_chunk_meta

            chunk = take_pending_chunk_meta(ctx, call_id) or None
        self.record_tool_result(call_id=call_id, content=str(content), chunk=chunk)
        self.mark_synced(cid)
        return call_id

    def record_assistant_tool_call(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        self._open_tool_call_id = call_id
        self.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call_entry(call_id, tool_name, arguments)],
            }
        )

    def record_assistant_tool_calls(
        self,
        calls: list[tuple[str, str, dict[str, Any]]],
    ) -> None:
        if not calls:
            return
        self._open_tool_call_id = calls[0][0] if len(calls) == 1 else ""
        self.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call_entry(call_id, tool_name, args) for call_id, tool_name, args in calls],
            }
        )

    def record_tool_result(
        self,
        *,
        call_id: str,
        content: str,
        chunk: dict[str, Any] | None = None,
    ) -> None:
        resolved = call_id or self._open_tool_call_id
        if not resolved:
            raise ValueError("tool result requires call_id (no matching assistant tool_call)")
        msg: dict[str, Any] = {"role": "tool", "tool_call_id": resolved, "content": content}
        if chunk:
            from mas.runtime.boundary.context.chunk_meta import attach_chunk_meta

            attach_chunk_meta(msg, chunk)
        self.messages.append(msg)
        if resolved == self._open_tool_call_id:
            self._open_tool_call_id = ""

    def record_assistant_message(self, content: str) -> None:
        if content.strip():
            self.messages.append({"role": "assistant", "content": content})
