#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Event parsing: events → typed execution records."""

import json
from collections import defaultdict
from typing import Any

from mas.lab.plots.multilevel_trajectory.constants import (
    _CALL_TYPE_TO_LEVEL,
    _KIND_BASE_TO_TYPE,
    _PROC_TYPE_LABEL,
    PROCESSING_NAME_CONTEXT_ASSEMBLY,
)
from mas.lab.plots.multilevel_trajectory.annotations import _derive_context_semantics


def _normalized_str(s: Any, default: str = "?") -> str:
    """Normalize a value to lowercase string, with default fallback."""
    return str(s or default).strip().lower() if s else default


def _event_loc(idx: Any, ev: dict[str, Any] | None = None, *, kind: Any = "", ts: Any = "") -> str:
    if ev is not None:
        kind = ev.get("kind", "")
        ts = ev.get("timestamp", "")
    return f"event #{idx} (kind={kind}, ts={ts})"


def _event_sig(ev: dict[str, Any]) -> str:
    return (
        f"kind={ev.get('kind','')} agent_id={ev.get('agent_id','')} "
        f"call_id={ev.get('call_id','')} parent_call_id={ev.get('parent_call_id','')} "
        f"ts={ev.get('timestamp','')}"
    )


def _dict_list(value: Any) -> list[dict] | None:
    if not isinstance(value, list):
        return None
    items = [item for item in value if isinstance(item, dict)]
    return items or None


def _normalize_context_messages(messages: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(messages, list):
        return out
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = _normalized_str(m.get("role"))
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        content = str(content).strip()
        if not content:
            continue
        out.append({"role": role, "content": content})
    return out


def _context_parts_from_messages(
    messages: list[dict[str, str]],
    *,
    prev_messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build context source records from assembled messages.

    Each part represents a piece of input context: its source, how it was
    included, what triggered its inclusion, and whether it's new compared to
    the previous context assembly.

    Attributes tracked for each context piece (what/where/why/when):
    - What: content (the actual text)
    - Where: source, sourceType, sourceId, actor, via (where it came from)
    - Why: decision, cause, trigger, causeType (why it was included)
    - When: highlight (is it new since last assembly)

    ``prev_messages`` is the input context from the previous LLM call for the
    same agent. A message is marked ``highlight=True`` (i.e. "new") when its
    (role, content) pair was not present in the previous context — meaning it
    was genuinely added since the last time the LLM was called. Tool results
    (including delegation replies) are marked as new on every appearance; system
    prompts are marked as new only when they first appear.

    When ``prev_messages`` is ``None`` (first call for this agent), every
    message is treated as new.
    """
    if prev_messages is None:
        # No history — every piece of context is new.
        prev_pairs: set[tuple[str, str]] = set()
        all_new = True
    else:
        prev_pairs = {
            (_normalized_str(m.get("role")), str(m.get("content") or "").strip())
            for m in prev_messages
            if str(m.get("content") or "").strip()
        }
        all_new = False

    parts: list[dict[str, Any]] = []
    for msg_idx, msg in enumerate(messages):
        role = _normalized_str(msg.get("role"))
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        is_new = all_new or (role, content) not in prev_pairs

        # Derive semantics from role, source, and content
        semantics = _derive_context_semantics(
            role, f"assembled/{role}",
            tool_call_id=_normalized_str(msg.get("tool_call_id")),
            content=content
        )
        trigger = semantics["trigger"]
        actor = semantics["actor"]
        via = semantics["via"]
        cause_type = semantics["causeType"]
        cause_detail = semantics["cause_detail"]

        parts.append({
            # What: the context content
            "content": content,
            # Where: source tracking
            "source": f"context/{role}",
            "sourceType": role,
            "sourceId": f"message_{msg_idx}",
            "actor": actor,
            "via": via,
            # Why: decision tracking
            "decision": "",  # empty unless specified in event data
            "cause": cause_detail,
            "causeType": cause_type,
            "trigger": trigger,
            # Category and mechanism (legacy, for compatibility)
            "category": "SYSTEM" if role == "system" else "CONTEXT",
            "mechanism": "inject",
            # Metadata
            "retrieval": "",
            "tokens": max(1, len(content) // 4),
            "retained": True,
            "placement": f"context/{role}",
            "sectionId": f"assembled/{msg_idx}/{role}",
            # Newness flag: True for context pieces added since the previous LLM call
            # Identifies delegation results, tool calls, and all other new turns
            "highlight": is_new,
        })
    # Sort so that newly-added context pieces appear LAST in the list.
    # This improves readability: existing context is shown first, then the
    # new additions that caused this context assembly. Uses stable sort to
    # preserve message order within each group (new vs existing).
    parts.sort(key=lambda p: p["highlight"])
    return parts

def _build_call_records(events: list[dict]) -> list[dict]:
    """Pair ``*_start`` / ``*_end`` events into typed execution record dicts.

    Each record has the keys::

        call_id, parent_call_id, call_type, level, agent_id,
        start_ts, end_ts, input, output, label, tool_name, model

    When the runtime emits ``call_id`` / ``parent_call_id`` on the event
    (observability_plugin ≥ v2), those values are used verbatim — a ``_start``
    and its ``_end`` are paired by that shared call_id directly (``open_by_id``
    below), never by comparing agent/type/name/timestamp. Older traces that
    lack ``call_id`` entirely fall back to a synthetic per-key FIFO pairing
    (``open_calls``) so ``_build_call_tree`` still has something to work with,
    but this path is never used once the runtime assigns real ids.
    """
    records: list[dict] = []
    open_by_id: dict[str, dict] = {}
    open_calls: dict[tuple, list[dict]] = defaultdict(list)
    call_seq:   dict[tuple, int]        = defaultdict(int)
    _STRICT_ID_TYPES = {
        "MASCall",
        "AgentCall",
        "LLMCall",
        "ToolCall",
        "MemoryCall",
        "RAGQuery",
    }
    # The native observability layer emits each engine op (LLM/tool/memory)
    # twice (~1ms apart: the operator wrapper + the engine adapter). Without
    # dedup, records.py builds two overlapping records per call, which corrupts
    # boundary alignment (a ToolCall inherits a later duplicate's end_ts and
    # overlaps the following LLM, so the renderer drops the tool bar). Dedup by
    # (correlation_id, kind) keeps the first start/end of each engine op.
    _seen_engine_io: set[tuple] = set()
    _ENGINE_IO_TYPES = {"LLMCall", "ToolCall", "MemoryCall", "RAGQuery"}

    # The native llm_call_start boundary carries no prompt (the engine builds
    # messages internally). The assembled prompt is emitted on the
    # context_assembled event with the same correlation_id — map it here so the
    # LLM record can show the prompt. (Order-independent: context_assembled may
    # be emitted after its llm_call_start.)
    _ca_messages_by_corr: dict[tuple, list] = {}
    for _e in events:
        if _e.get("kind") == "context_assembled" and _e.get("messages"):
            _c = _e.get("correlation_id")
            _a = _e.get("agent_id")
            if _c is not None and _a is not None:
                _ca_messages_by_corr[(_a, _c)] = _e["messages"]

    # A tool call is emitted twice under the same call_id: once by the generic
    # envelope-activity wrapper (tool_name "contract_call") and once by the
    # engine-io boundary carrying the real tool name (lookup_schedule,
    # get_fares, delegate_to_<id>, …).  Map each call_id to its specific name so
    # records show the real tool rather than the generic wrapper.
    _GENERIC_TOOL_NAMES = {"contract_call", ""}
    _tool_name_by_call: dict[str, str] = {}
    _tool_args_by_call: dict[str, Any] = {}
    for _e in events:
        if "tool_call" not in (_e.get("kind") or ""):
            continue
        _cid2 = _e.get("call_id")
        if not _cid2:
            continue
        _tn2 = _e.get("tool_name") or ""
        if _tn2 not in _GENERIC_TOOL_NAMES:
            _tool_name_by_call.setdefault(_cid2, _tn2)
        # The real arguments ride on the engine-io emission, not the generic
        # envelope wrapper — capture them so the tool bar shows its call args.
        _args2 = _e.get("arguments")
        if _cid2 not in _tool_args_by_call and isinstance(_args2, dict) and _args2:
            _tool_args_by_call[_cid2] = _args2

    # Per-agent previous context: tracks the assembled context from each agent's
    # previous LLM call, used to compute diffs for highlighting what actually
    # changed (newly added context) vs what was carried over.
    _prev_context_by_agent: dict[str, list[dict[str, str]]] = {}

    for _ev_idx, ev in sorted(enumerate(events), key=lambda p: float(p[1].get("timestamp") or 0)):
        kind      = ev.get("kind", "")
        kind_base = kind.replace("_start", "").replace("_end", "")
        agent_id  = ev.get("agent_id", "")
        tool_name = ev.get("tool_name", "")
        # Resolve generic "contract_call" wrappers to the real tool name so the
        # start/end pair on a consistent key and the bar shows the true tool.
        if "tool_call" in kind and tool_name in _GENERIC_TOOL_NAMES:
            _rn = _tool_name_by_call.get(ev.get("call_id") or "")
            if _rn:
                tool_name = _rn
                ev = {**ev, "tool_name": _rn}  # so _build_call_label sees it too
        call_type = _KIND_BASE_TO_TYPE.get(kind_base)
        if call_type is None:
            continue
        if call_type in _STRICT_ID_TYPES and not ev.get("call_id"):
            raise ValueError(
                "Trajectory parsing requires runtime call_id on all call boundary events; "
                f"missing call_id for {_event_sig(ev)} ({_event_loc(_ev_idx, ev)}). "
                "Likely cause: the trace predates call ids or was produced by a non-runtime writer"
            )
        # Drop duplicate engine-op emissions (see _seen_engine_io above).
        # Key on agent_id too: correlation_id restarts per agent, so a peer's
        # LLM (corr=1) must not be mistaken for the entry agent's LLM (corr=1)
        # in a multi-agent run — that would delete the peer's calls entirely.
        if call_type in _ENGINE_IO_TYPES:
            _corr = ev.get("correlation_id")
            if _corr is not None:
                _io_key = (agent_id, _corr, kind_base, kind.endswith("_end"))
                if _io_key in _seen_engine_io:
                    continue
                _seen_engine_io.add(_io_key)
        # Skip self-referential start events: these are inner-layer duplicate
        # emissions where the engine adapter reuses the same call_id that the
        # ObservabilityOperator wrapper already pushed as a frame.
        # Symptom: parent_call_id == call_id (a call cannot be its own parent).
        # Keeping both would produce two records with the same call_id — the
        # inner (orphaned) record's end_ts defaults to start + 1.0 s and
        # causes boundary-alignment to corrupt unrelated records' timestamps.
        if kind.endswith("_start"):
            _cid = ev.get("call_id")
            _pid = ev.get("parent_call_id")
            if _cid and _pid and _cid == _pid:
                raise ValueError(
                    "Invalid self-parent call boundary: call_id equals parent_call_id "
                    f"for {_event_sig(ev)}"
                )
        # Promote MITM processing calls to a dedicated visual type.
        if call_type == "ProcessingCall" and ev.get("processing_type") == "mitm_rewrite":
            call_type = "MITMCall"
        key = (agent_id, kind_base, tool_name)

        # Attach the assembled prompt to LLM starts that lack inline messages.
        if kind.endswith("_start") and call_type == "LLMCall" and not ev.get("messages"):
            _mc = _ca_messages_by_corr.get((agent_id, ev.get("correlation_id")))
            if _mc:
                ev = {**ev, "messages": _mc}

        if kind.endswith("_start"):
            _real_id = ev.get("call_id")
            if _real_id:
                call_id = _real_id
            else:
                if call_type in _STRICT_ID_TYPES:
                    raise ValueError(
                        "Trajectory parsing requires runtime call_id on start events; "
                        f"cannot synthesize id for {_event_sig(ev)}"
                    )
                seq = call_seq[key]
                call_seq[key] += 1
                call_id = f"{kind_base}-{agent_id}-{seq}"
            record: dict[str, Any] = {
                "call_id":        call_id,
                "parent_call_id": ev.get("parent_call_id"),  # None for roots / old traces
                "_has_ids":  "call_id" in ev,  # True when runtime emitted the field
                "call_type": call_type,
                "level":     _CALL_TYPE_TO_LEVEL.get(call_type, "call"),
                "agent_id":  agent_id,
                "start_ts":  float(ev.get("timestamp") or 0),
                "end_ts":    0.0,
                "input":     ev.get("input") or ev.get("prompt") or "",
                "output":    "",
                "label":     _build_call_label(call_type, ev),
                "tool_name": tool_name,
                "model":     ev.get("model", ""),
                "thinking":  "",
                "correlation_id": ev.get("correlation_id"),
                "processing_name": ev.get("processing_name", "") if call_type == "ProcessingCall" else "",
            }
            if call_type == "ProcessingCall":
                record["processing_type"] = str(ev.get("processing_type") or "")
                if ev.get("group_id") is not None:
                    record["group_id"] = str(ev.get("group_id") or "")
                if ev.get("tool_count") is not None:
                    record["tool_count"] = ev.get("tool_count")
                if isinstance(ev.get("tools"), list):
                    record["tools"] = list(ev.get("tools") or [])
                if ev.get("wait_link_id") is not None:
                    record["wait_link_id"] = str(ev.get("wait_link_id") or "")
                if ev.get("wait_role") is not None:
                    record["wait_role"] = str(ev.get("wait_role") or "")
                if ev.get("wait_note") is not None:
                    record["wait_note"] = str(ev.get("wait_note") or "")
                if ev.get("wait_scope") is not None:
                    record["wait_scope"] = str(ev.get("wait_scope") or "")
            if call_type == "ProcessingCall":
                parts = _dict_list(ev.get("segments"))
                if parts:
                    record["segments"] = parts
                record["context_operation"] = str(ev.get("context_operation") or "")
                _ctx_msgs = _normalize_context_messages(
                    ev.get("messages")
                    or _ca_messages_by_corr.get((agent_id, ev.get("correlation_id")))
                )
                if _ctx_msgs:
                    record["context_messages"] = _ctx_msgs
                    record["context_parts"] = _context_parts_from_messages(
                        _ctx_msgs,
                        prev_messages=_prev_context_by_agent.get(agent_id)
                    )
                    # Update tracking: this context assembly is now the "previous"
                    # for the next LLM call from this same agent. correlation_id 0
                    # is the trace-preview sentinel (LiveLlmEngine.exchange_preview
                    # sets _assembly_correlation_id=0) — not a real dispatched call,
                    # so it must not poison the "previous context" a real call
                    # diffs against, or the real call's own diff comes out empty.
                    if ev.get("correlation_id"):
                        _prev_context_by_agent[agent_id] = _ctx_msgs
            if call_type == "LLMCall" and ev.get("messages"):
                msgs = ev["messages"]
                if isinstance(msgs, list) and msgs:
                    # The LLM input is the *full assembled context* actually sent
                    # to the model — the system prompt concatenated with the user
                    # turn (and, on follow-up calls, the conversation history and
                    # tool / sub-agent results).  This is the output of the
                    # preceding ⚙ context node, shown on the state between it and
                    # the LLM bar.  The user-entry state (S1) keeps the bare user
                    # question; the system prompt only appears here, post-assembly.
                    lines: list[str] = []
                    for m in msgs:
                        role = m.get("role", "?")
                        content = m.get("content") or ""
                        if isinstance(content, list):
                            content = " ".join(
                                p.get("text", "") for p in content if isinstance(p, dict)
                            )
                        if not content:
                            continue
                        _label = "TOOL RESULT" if role == "tool" else role.upper()
                        lines.append(f"[{_label}]\n{str(content)}")
                    if lines:
                        record["input"] = "\n\n---\n\n".join(lines)
            if call_type == "ToolCall" and not record["input"]:
                # tool_call_start stores params in 'arguments', not 'input'.
                # Resolve from the engine-io emission (the generic envelope
                # wrapper carries no arguments) so the bar shows the real args.
                raw_args = ev.get("arguments") or _tool_args_by_call.get(ev.get("call_id") or "")
                if raw_args is not None and str(raw_args).strip() not in ("", "None", "null", "{}"):
                    _tn = ev.get("tool_name") or tool_name or "tool"
                    if isinstance(raw_args, dict):
                        _argstr = ", ".join(f"{k}={v!r}" for k, v in raw_args.items())
                    else:
                        _argstr = str(raw_args)
                    record["input"] = f"→ {_tn}({_argstr})"
                else:
                    # At minimum show which tool is being called
                    record["input"] = f"→ {ev.get('tool_name') or tool_name or 'tool'}()"
            if call_type == "ProcessingCall" and not record["input"]:
                # Fallback: reconstruct a readable summary from the extra fields
                # emitted by the runtime (sections, evicted_sections, etc.).
                _pt = ev.get("processing_type", "")
                if _pt == "memory_injection":
                    _n = len(ev.get("sections") or [])
                    _t = ev.get("tokens") or 0
                    record["input"] = f"{_n} section{'s' if _n != 1 else ''} · {_t} tok"
                elif _pt == "ontology_injection":
                    _n = len(ev.get("sections") or [])
                    record["input"] = f"{_n} section{'s' if _n != 1 else ''}"
                elif _pt == "context_compression":
                    _n = len(ev.get("evicted_sections") or [])
                    _t = ev.get("evicted_tokens") or 0
                    record["input"] = f"{_n} evicted · {_t} tok"
                elif _pt == "context_paging":
                    _n = ev.get("summarized_turns") or 0
                    record["input"] = f"{_n} turn{'s' if _n != 1 else ''} summarized"
                elif _pt:
                    record["input"] = _pt
            if call_type == "ContextState":
                action = str(ev.get("update_type") or "mutation")
                turn = ev.get("turn_index")
                wm = ev.get("wm_count")
                committed = ev.get("committed_count")
                record["update_type"] = action
                record["input"] = (
                    f"{action}"
                    + (f" · turn {turn}" if turn is not None else "")
                    + (f" · wm={wm}" if wm is not None else "")
                    + (f" · hist={committed}" if committed is not None else "")
                )
            if _real_id and call_id in open_by_id:
                _stale = open_by_id[call_id]
                if _stale.get("level") != "agent":
                    first = _stale.get("_opened_by", ("?", "", ""))
                    raise ValueError(
                        "Duplicate open call_id for non-agent record; this indicates broken "
                        "runtime boundaries. "
                        f"existing={_stale.get('call_type')} new={record.get('call_type')} "
                        f"call_id={call_id}: first opened by "
                        f"{_event_loc(first[0], kind=first[1], ts=first[2])}, opened again by "
                        f"{_event_loc(_ev_idx, ev)} before the first one ended. "
                        "Likely cause: the same call was recorded twice"
                    )
                # Agent-level id reuse is expected in some runtimes (e.g.
                # repeated delegate invocations sharing an execution id).
                _stale = open_by_id.pop(call_id)
                if _stale.get("end_ts", 0) <= 0:
                    _stale["end_ts"] = _stale["start_ts"] + 1.0
                    _stale["_end_missing"] = True
                _stale.setdefault("parent_call_id", None)
                records.append(_stale)
            if _real_id:
                record["_opened_by"] = (_ev_idx, kind, ev.get("timestamp", ""))
                open_by_id[call_id] = record
            else:
                open_calls[key].append(record)

        elif kind.endswith("_end"):
            # Pair by the shared call_id the runtime assigned to both this
            # event and its _start — never by comparing agent/type/name or
            # timestamps. Only a trace old enough to omit call_id entirely
            # falls back to the legacy per-key FIFO bucket below.
            _end_cid = ev.get("call_id")
            if call_type in _STRICT_ID_TYPES and not _end_cid:
                raise ValueError(
                    "Trajectory parsing requires runtime call_id on end events; "
                    f"cannot match {_event_sig(ev)}"
                )
            record = None
            if _end_cid and _end_cid in open_by_id:
                record = open_by_id.pop(_end_cid)
            elif open_calls[key]:
                record = open_calls[key].pop(0)
            if record is None:
                if call_type in _STRICT_ID_TYPES:
                    raise ValueError(
                        "Orphan end event: no matching open start with same call_id; "
                        f"{_event_sig(ev)} ({_event_loc(_ev_idx, ev)}). "
                        "Likely cause: start missing, end recorded twice, or truncated trace"
                    )
                continue
            record["end_ts"] = float(ev.get("timestamp") or 0)
            _ex_status = ev.get("status") or "success"
            if _ex_status not in ("success", "ok", ""):
                record["_exec_status"] = _ex_status
            out = ev.get("output") or ev.get("result") or ""
            if call_type == "LLMCall":
                # ``next_step`` (e.g. "TOOL_CALL"/"PARALLEL_TOOL_CALLS") is the
                # native, authoritative signal that this call's real "output"
                # was a tool-call decision, not text — llm_call_end.output is
                # legitimately empty in that case. Preserved on the record so
                # _collect_llm_tool_decisions can synthesize the real decision
                # from the next ToolCall/parallel_group record.
                if ev.get("next_step"):
                    record["next_step"] = ev.get("next_step")
                _thinking = ev.get("thinking") or (ev.get("response") or {}).get("thinking") or ""
                if _thinking:
                    record["thinking"] = _thinking
                resp = ev.get("response") or {}
                if isinstance(resp, dict):
                    # Support both formats:
                    #   canonical OpenAI: choices[0].message.{content, tool_calls}
                    #   flat runtime:     {content, tool_calls, usage}
                    choices = resp.get("choices") or []
                    if choices:
                        msg        = choices[0].get("message") or {}
                        content    = msg.get("content") or ""
                        tool_calls = msg.get("tool_calls") or []
                    else:
                        content    = str(resp.get("content") or "")
                        tool_calls = resp.get("tool_calls") or []

                    parts: list[str] = []
                    if content:
                        parts.append(content)
                    if tool_calls:
                        tc_lines: list[str] = []
                        for tc in tool_calls:
                            fn   = tc.get("function") or {}
                            name = fn.get("name") or tc.get("name") or "?"
                            args = fn.get("arguments") or tc.get("arguments") or ""
                            if not isinstance(args, str):
                                args = json.dumps(args, ensure_ascii=False)
                            tc_lines.append(f"[Tool call] → {name}({args})")
                        parts.append("\n".join(tc_lines))
                    if parts:
                        out = "\n\n".join(parts)
            out_str = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            record["output"] = out_str
            # Fallback: ProcessingCall output from processing_output field (end event)
            if call_type == "ProcessingCall" and not record["output"]:
                record["output"] = ev.get("processing_output") or record.get("input", "")
            if call_type == "ProcessingCall":
                if not record.get("group_id") and ev.get("group_id") is not None:
                    record["group_id"] = str(ev.get("group_id") or "")
                if (not record.get("tools")) and isinstance(ev.get("tools"), list):
                    record["tools"] = list(ev.get("tools") or [])
                if record.get("tool_count") is None and ev.get("tool_count") is not None:
                    record["tool_count"] = ev.get("tool_count")
                if not record.get("wait_link_id") and ev.get("wait_link_id") is not None:
                    record["wait_link_id"] = str(ev.get("wait_link_id") or "")
                if not record.get("wait_role") and ev.get("wait_role") is not None:
                    record["wait_role"] = str(ev.get("wait_role") or "")
                if not record.get("wait_note") and ev.get("wait_note") is not None:
                    record["wait_note"] = str(ev.get("wait_note") or "")
                if not record.get("wait_scope") and ev.get("wait_scope") is not None:
                    record["wait_scope"] = str(ev.get("wait_scope") or "")
            if call_type == "ProcessingCall" and record.get("processing_name") == PROCESSING_NAME_CONTEXT_ASSEMBLY:
                _ctx_msgs = _normalize_context_messages(
                    ev.get("messages")
                    or _ca_messages_by_corr.get((record.get("agent_id"), record.get("correlation_id")))
                )
                if _ctx_msgs and not record.get("context_messages"):
                    record["context_messages"] = _ctx_msgs
                    _agent_id = record.get("agent_id") or ""
                    record["context_parts"] = _context_parts_from_messages(
                        _ctx_msgs,
                        prev_messages=_prev_context_by_agent.get(_agent_id)
                    )
                    # Update tracking: this context assembly is now the "previous"
                    # for the next LLM call from this same agent. correlation_id 0
                    # is the trace-preview sentinel (LiveLlmEngine.exchange_preview
                    # sets _assembly_correlation_id=0) — not a real dispatched call;
                    # see the same guard in the sibling loop above in this file.
                    if record.get("correlation_id"):
                        _prev_context_by_agent[_agent_id] = _ctx_msgs
                if not record.get("context_operation"):
                    record["context_operation"] = str(ev.get("context_operation") or "")
                if record.get("context_messages") and (not record.get("output") or str(record.get("output", "")).strip() in {"assembled", PROCESSING_NAME_CONTEXT_ASSEMBLY, "context_assembly"}):
                    # Keep context assembly as structured CPR only (parts/messages),
                    # not as a synthetic textual diff block.
                    record["output"] = ""
            if call_type == "ContextState" and not record["output"]:
                record["output"] = str(ev.get("content_preview") or ev.get("output") or "")
            records.append(record)

    # Close any records whose _end event was missing.
    for pending_list in list(open_calls.values()) + [list(open_by_id.values())]:
        for rec in pending_list:
            if rec.get("end_ts", 0) <= 0:
                rec["end_ts"] = rec["start_ts"] + 1.0
                rec["_end_missing"] = True
            # Ensure parent_call_id key is always present (may be None for roots).
            rec.setdefault("parent_call_id", None)
        records.extend(pending_list)

    # NOTE: engine-op "twin matching" used to live here — reconstructing which
    # of two same-agent/same-tool calls an ``_end_missing`` record actually
    # belonged to by comparing tool_name + start_ts within ``_TS_TOL``. That
    # heuristic existed only because the runtime never emitted
    # tool_call_end/memory_call_end for ANY call (a dead-code bug in
    # obs_envelope.py's OBSERVABILITY_POST_EXECUTE handling — see
    # runtime/src/mas/runtime/machines/obs_envelope.py and
    # runtime/src/mas/runtime/kernel/ingress_step.py's op misclassification
    # for MEMORY_OP). Now that both are fixed, every engine-op call gets its
    # own real end event with the SAME call_id its own start used — verified
    # by instrumenting this exact matching logic against all 4 golden
    # fixtures (lifecycle-control, extensions, design-space, lab-smoke) and
    # confirming it never fires. ``_end_missing`` can now only mean a call
    # that was genuinely still open when the trace was cut (handled below by
    # the t_final/+1.0s fallback), not a duplicate to reconcile.

    # NOTE: a timestamp-based "impossible children" reparenting pass used to
    # live here — walking a record's parent_call_id chain and promoting it to
    # its grandparent whenever start_ts landed at/after the parent's own
    # end_ts (within _TS_TOL). Removed outright: parent_call_id is a real,
    # runtime-assigned fact (see _build_call_tree), never something a
    # timestamp comparison gets to override — timestamps are for display/
    # ordering only. The apparent "impossible" cases this used to "fix" were
    # never a wrong parent link; they were an artifact of a timestamp-
    # fabrication bug in library-standard's native export layer
    # (dispatch_boundary re-stamping every boundary event with the async
    # export-time `time.time()` instead of the real occurrence-time
    # TransitionEvent.timestamp — see project.py's boundary_dict_from_transition
    # and boundary_handlers.py's dispatch_boundary), which made an agent's own
    # tool_call_end appear to land after its own execution_end even though it
    # truly happened first. That bug is fixed at its real source; reparenting
    # based on the (now-correct) timestamps would still have been the wrong
    # mechanism even when it happened to look right.

    # NOTE: delegated sub-agent executions no longer need to be re-linked
    # here. The runtime now threads a real `caller_call_id` through the
    # delegation contract itself (InvokeEngineIo.call_id -> execute_engine_tool
    # -> DelegationContract -> RunTurnFn, resolved once by the driver via
    # ObservabilityOperator.call_id_for right before the engine is invoked —
    # see runtime/src/mas/runtime/driver/driver.py), so a peer's own
    # execution_start.parent_call_id already IS the delegating tool call's
    # own call_id on the wire, for every delegation depth (including nested
    # ones — see ctl/.../mas_session.py's wire_peer_delegation). No
    # timestamp-proximity or tool-name reconstruction needed: _build_call_tree
    # already builds the correct tree from parent_call_id alone.

    # Fix synthetic end_ts for governance-orphaned LLMCalls.
    # When governance fires between two llm_call_start events, the first call
    # never receives an llm_call_end and gets the placeholder end_ts of
    # start_ts + 1.0.  Replace that with the start_ts of the chronologically
    # next sibling LLMCall so that the orphan's duration is accurate (≈ the
    # time until governance handed off to the next call) and child records
    # (e.g. memory_search ToolCalls nested under the orphan) don't get their
    # boundaries inflated by _align_record_boundaries Rule 3.
    _by_parent: dict[Any, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("call_type") == "LLMCall":
            _by_parent[r.get("parent_call_id")].append(r)
    for _siblings in _by_parent.values():
        if len(_siblings) < 2:
            continue
        _llm_sorted = sorted(_siblings, key=lambda r: r["start_ts"])
        for _idx, _llm in enumerate(_llm_sorted):
            if not _llm.get("_end_missing"):
                continue
            # Find the next sibling that starts strictly after this one.
            # Use a 1 ms tolerance (not _TS_TOL=50 ms) so that even rapid
            # governance short-circuits (< 50 ms) get their end_ts fixed.
            for _j in range(_idx + 1, len(_llm_sorted)):
                _nxt = _llm_sorted[_j]
                if _nxt["start_ts"] > _llm["start_ts"] + 0.001:
                    _llm["end_ts"] = _nxt["start_ts"]
                    break

    # Agent records that never received an execution_end event were still
    # executing when the trace was cut.  Using start_ts + 1.0 as their end
    # would truncate their call window and cause call-lane children to be
    # silently dropped by the _collect window filter.  Instead, extend them
    # to t_final — the last known timestamp in the trace — which is the
    # structurally correct boundary: "running until end of observation".
    _t_final = max((r["end_ts"] for r in records), default=0.0)
    for rec in records:
        if rec.get("_end_missing") and rec.get("level") == "agent":
            rec["end_ts"] = _t_final

    # Real processing events for context assembly may be emitted with timestamps
    # that are equal to, or even slightly after, the target llm_call_start.
    # For the trajectory chain semantics we enforce: S_pre -> ⚙ context -> S_post -> LLM.
    _llm_by_key: dict[tuple[str, Any], dict] = {
        (str(r.get("agent_id") or ""), r.get("correlation_id")): r
        for r in records
        if r.get("call_type") == "LLMCall" and r.get("correlation_id") is not None
    }
    _ends_by_agent: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.get("level") == "call" and r.get("call_type") != "ProcessingCall":
            _ends_by_agent[str(r.get("agent_id") or "")].append(float(r.get("end_ts") or 0.0))
    for rec in records:
        if rec.get("call_type") != "ProcessingCall":
            continue
        if str(rec.get("processing_name") or "").strip().lower() != PROCESSING_NAME_CONTEXT_ASSEMBLY.lower():
            continue
        _aid = str(rec.get("agent_id") or "")
        _corr = rec.get("correlation_id")
        if _corr is None:
            continue
        _llm = _llm_by_key.get((_aid, _corr))
        if _llm is None:
            continue
        _llm_start = float(_llm.get("start_ts") or 0.0)
        rec["end_ts"] = _llm_start
        rec["start_ts"] = max(
            (e for e in _ends_by_agent.get(_aid, []) if e <= _llm_start + 1e-9),
            default=min(float(rec.get("start_ts") or _llm_start), _llm_start - 0.001),
        )
        if rec["start_ts"] >= rec["end_ts"]:
            rec["start_ts"] = rec["end_ts"] - 0.001

    # Some runtimes emit preflight context_assembled events with correlation_id=0
    # and no corresponding llm_call. Keep context-assembly ProcessingCall only
    # when it maps to a real LLM call so the call lane stays semantically ordered.
    _valid_ctx_keys: set[tuple[str, Any]] = {
        (str(r.get("agent_id") or ""), r.get("correlation_id"))
        for r in records
        if r.get("call_type") == "LLMCall" and r.get("correlation_id") is not None
    }
    records = [
        r for r in records
        if not (
            r.get("call_type") == "ProcessingCall"
            and str(r.get("processing_name") or "").strip().lower() == PROCESSING_NAME_CONTEXT_ASSEMBLY.lower()
            and r.get("correlation_id") is not None
            and (str(r.get("agent_id") or ""), r.get("correlation_id")) not in _valid_ctx_keys
        )
    ]

    # Multiple agents may share the same runtime call_id (e.g. u1-exec); ensure
    # agent-lane records remain uniquely addressable in the call tree. A single
    # agent delegated to repeatedly (e.g. schedule_agent 3+ times in one turn)
    # reuses that same runtime id every time, so a one-shot "-{agent_id}"
    # suffix isn't enough — the 2nd, 3rd, ... collision would all rename to
    # the identical suffixed id and collide again. Keep suffixing with an
    # incrementing counter until the result is actually unseen: every branch
    # needs its own real, addressable call_id (see tree.py's fork/branch
    # detection, which identifies a branch by its own child call_id).
    # Renaming only relabels the AgentCall record itself — every LLMCall /
    # ToolCall the repeated invocation makes still carries the *original*
    # (reused) id as its own ``parent_call_id`` on the wire, because the
    # runtime has no notion of the suffix minted here. Remember that original
    # id as ``_reused_call_id`` so tree.py can still find this invocation's
    # real children: they live in ``children_of[cid]`` (the shared pool for
    # every invocation of this agent), not ``children_of[_new_id]`` (which is
    # empty for every invocation but the first).
    _seen_ids: set[str] = set()
    for rec in records:
        cid = str(rec.get("call_id") or "")
        if not cid:
            continue
        if cid in _seen_ids and rec.get("level") == "agent":
            _base = f"{cid}-{rec.get('agent_id', 'agent')}"
            _new_id = _base
            _n = 2
            while _new_id in _seen_ids:
                _new_id = f"{_base}-{_n}"
                _n += 1
            rec["_reused_call_id"] = cid
            rec["call_id"] = _new_id
        _seen_ids.add(str(rec["call_id"]))

    records.extend(_synthesize_context_processing_records(events, records, _prev_context_by_agent))
    _attach_parentless_calls(records)

    return sorted(records, key=lambda r: r["start_ts"])


def _attach_parentless_calls(records: list[dict]) -> None:
    """If exactly one agent execution contains a parentless call, make it the parent."""
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if rec.get("level") == "agent":
            by_agent[str(rec.get("agent_id") or "")].append(rec)
    for rec in records:
        if rec.get("level") != "call" or rec.get("parent_call_id"):
            continue
        start = float(rec.get("start_ts") or 0)
        owners = [
            agent
            for agent in by_agent.get(str(rec.get("agent_id") or ""), [])
            if float(agent.get("start_ts") or 0) <= start <= float(agent.get("end_ts") or 0)
        ]
        if len(owners) == 1:
            rec["parent_call_id"] = owners[0]["call_id"]
            rec["_parent_inferred"] = True


def _synthesize_context_processing_records(
    events: list[dict], records: list[dict],
    prev_context_by_agent: dict[str, list[dict[str, str]]],
) -> list[dict]:
    """Create a ProcessingCall (context-assembly) node before each LLM call.

    The native trace emits a ``context_assembled`` event carrying the fully
    assembled messages (system prompt + injected context) right before each LLM
    call, but no ``processing_call`` event (legacy KG traces had those).  Without
    a node, the system prompt/context has nowhere to live and leaks into the LLM
    or user-input bar.  Synthesize the "first transformation inside the agent":
    a small ProcessingCall that shows the system prompt and injected context,
    sitting between the incoming turn and the LLM call it feeds.
    """
    llm_by_key: dict[tuple, dict] = {}
    for r in records:
        if r.get("call_type") == "LLMCall" and r.get("correlation_id") is not None:
            llm_by_key[(r.get("agent_id"), r["correlation_id"])] = r

    # Per-agent, the end timestamps of real (non-processing) call records — used
    # to anchor each context node right after the call that precedes its LLM (a
    # tool result / prior LLM), so it never overlaps a tool that finished only a
    # fraction of a millisecond before the LLM started.
    _ends_by_agent: dict[str, list[float]] = {}
    for r in records:
        if r.get("level") == "call" and r.get("call_type") != "ProcessingCall":
            _ends_by_agent.setdefault(r.get("agent_id", ""), []).append(r["end_ts"])

    out: list[dict] = []
    seen: set[tuple] = set()
    real_context_processing: set[tuple[str, Any]] = {
        (str(r.get("agent_id") or ""), r.get("correlation_id"))
        for r in records
        if r.get("call_type") == "ProcessingCall"
        and str(r.get("processing_name") or "").strip().lower() == "context assembly"
        and r.get("correlation_id") is not None
    }
    for ev in sorted(events, key=lambda e: float(e.get("timestamp") or 0)):
        if ev.get("kind") != "context_assembled":
            continue
        msgs = ev.get("messages") or []
        if not msgs:
            continue
        aid = ev.get("agent_id", "")
        corr = ev.get("correlation_id")
        llm = llm_by_key.get((aid, corr))
        if llm is None:  # skip the duplicate corr=0 emission and any unmatched
            continue
        if (str(aid or ""), corr) in real_context_processing:
            continue
        key = (aid, corr)
        if key in seen:
            continue
        seen.add(key)
        # The assembled context (system prompt + injected segments) is the
        # *output* of this step — it must NOT land on the preceding state (which
        # should show the incoming turn: the user question or a tool result).
        # dag.py clears a ProcessingCall's output from the following state, so
        # the system prompt shows only on the ⚙ context bar itself.
        context_messages = _normalize_context_messages(msgs)
        context_parts = _context_parts_from_messages(
            context_messages,
            prev_messages=prev_context_by_agent.get(aid)
        )
        # Update tracking: this context assembly is now the "previous"
        # for the next LLM call from this same agent.
        prev_context_by_agent[aid] = context_messages
        cid = f"ctxasm-{aid}-{corr}"
        operation = "PREPEND" if any(msg.get("role") == "system" for msg in context_messages) else "APPEND"
        out.append({
            "call_id": cid,
            "parent_call_id": llm.get("parent_call_id"),
            "_has_ids": True,
            "call_type": "ProcessingCall",
            "level": "call",
            "agent_id": aid,
            # Sit between the preceding call and this LLM: anchor the start at the
            # latest real call that ended before the LLM (a tool result / prior
            # LLM) so the node never overlaps a tool that finished a fraction of a
            # millisecond earlier.  Fall back to a tiny lead when nothing precedes
            # (the agent's first call); Rule 1 then widens it to the agent start.
            "start_ts": max(
                (e for e in _ends_by_agent.get(aid, []) if e <= llm["start_ts"] + 1e-9),
                default=llm["start_ts"] - 0.001,
            ),
            "end_ts": llm["start_ts"],
            # input drives the preceding state — leave empty so the incoming turn
            # (user question / tool result) shown there is not overwritten.
            "input": "",
            # Keep assembly semantics in structured fields only.
            "output": "",
            "label": "⚙ context",
            "tool_name": "",
            "model": "",
            "thinking": "",
            "correlation_id": corr,
            "processing_name": "context assembly",
            "context_operation": operation,
            "context_messages": context_messages,
            "context_parts": context_parts,
        })
    return out


def _build_call_label(call_type: str, ev: dict) -> str:
    _fn: dict[str, Any] = {
        "MASCall":        lambda e: e.get("mas_name") or e.get("agent_id") or "MAS",
        "AgentCall":      lambda e: e.get("agent_id") or "agent",
        "LLMCall":        lambda e: e.get("model") or "LLM",
        "ToolCall":       lambda e: e.get("tool_name") or e.get("endpoint") or "tool",
        "MemoryCall":     lambda e: e.get("memory_type") or "memory",
        "RAGQuery":       lambda e: "RAG",
        "MITMCall":       lambda e: "⚠ MITM",
        "ProcessingCall": lambda e: (
            _PROC_TYPE_LABEL.get(e.get("processing_type", ""), "")
            or e.get("processing_name")
            or e.get("skill_name")
            or "proc"
        ),
        "ContextState":   lambda e: str(e.get("update_type") or "state")[:22],
    }
    return str(_fn.get(call_type, lambda e: call_type)(ev))[:22]


def _extract_user_input(events: list[dict]) -> str:
    for ev in sorted(events, key=lambda e: float(e.get("timestamp") or 0)):
        if ev.get("kind") == "execution_start":
            ctx = ev.get("context") or {}
            if isinstance(ctx, dict) and ctx.get("is_entry_agent") and ev.get("input"):
                return str(ev["input"])
    for ev in sorted(events, key=lambda e: float(e.get("timestamp") or 0)):
        if ev.get("kind") == "execution_start" and ev.get("input"):
            return str(ev["input"])
    return ""


def _extract_final_output(events: list[dict]) -> str:
    # ``execution_end`` carries the final answer as its own ``output``/
    # ``payload`` field on SOME trace formats, but native observability's
    # own ``execution_end`` event for a plain (non-delegating) agent turn
    # carries only ``status`` — the real final text instead sits on that
    # same turn's own ``user_response`` event (its ``content`` field, timed
    # a hair earlier — both mark the exact same "answer handed back" moment
    # from independent instrumentation points). Missing this source is why
    # a real trace's own final answer state at the very end of the whole
    # trace could show up with no content, despite the underlying
    # execution genuinely producing one.
    candidates = [
        (float(e.get("timestamp") or 0), str(e.get("output") or e.get("payload") or ""))
        for e in events
        if e.get("kind") == "execution_end" and (e.get("output") or e.get("payload"))
    ] + [
        (float(e.get("timestamp") or 0), str(e.get("content") or ""))
        for e in events
        if e.get("kind") == "user_response" and e.get("content")
    ]
    if not candidates:
        return ""
    return max(candidates, key=lambda c: c[0])[1]


# ---------------------------------------------------------------------------
# Thinking records — synthesised from LLMCall records with thinking content
# ---------------------------------------------------------------------------

def _synthesize_thinking_records(records: list[dict]) -> list[dict]:
    """Create synthetic ThinkingCall records from LLMCall records that carry thinking text.

    Thinking occupies the first ~85 % of the LLM call wall-clock duration.
    The synthetic end timestamp is:  start + (end - start) * 0.85
    """
    result: list[dict] = []
    for rec in records:
        thinking_text = rec.get("thinking", "")
        if rec.get("call_type") != "LLMCall" or not thinking_text:
            continue
        duration = rec["end_ts"] - rec["start_ts"]
        thinking_end_ts = (
            rec["start_ts"] + duration * 0.85 if duration > 0.0
            else rec["end_ts"] - 0.1
        )
        result.append({
            "call_id":        rec["call_id"] + "-thinking",
            "parent_call_id": rec["call_id"],
            "_has_ids":       True,
            # Internal field — used to retrieve the LLM call end for the thinking lane.
            "_llm_end_ts":    rec["end_ts"],
            "call_type":      "ThinkingCall",
            "level":          "thinking",
            "agent_id":       rec["agent_id"],
            "start_ts":       rec["start_ts"],
            "end_ts":         thinking_end_ts,
            # Input = the LLM prompt that triggered the thinking
            "input":          rec.get("input", ""),
            "output":         thinking_text,
            # Carry the real LLM response for the ThinkingEmit hover_out
            "llm_output":     rec.get("output", ""),
            "label":          "Think",
            "tool_name":      "",
            "model":          rec.get("model", ""),
            "thinking":       "",
        })
    return result
