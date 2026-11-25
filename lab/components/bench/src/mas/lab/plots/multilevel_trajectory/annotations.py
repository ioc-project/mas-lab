#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Annotation and context-provenance hover enrichment."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from mas.lab.plots.multilevel_trajectory.constants import _STAGGER_DUR, _TS_TOL

# State hover priority: the most specific (deepest) level's output wins when
# multiple transitions end at the same timestamp.
# ``call_end`` (=4) is used at a tool/processing call's own result boundary so
# its real output takes precedence over an immediately-following record's
# generic bookkeeping value that happens to share the exact same ts (observed
# in a real trace: a parallel-group branch's own tool-result end state getting
# silently overwritten by the next processing step's generic label).
# ``agent_end`` (=5) is used at agent-result boundaries so the agent's
# authoritative output takes precedence over both call-layer and call_end
# values sharing the same ts.
_HOVER_PRIORITY: dict[str, int] = {
    "session": 0,
    "mas": 1,
    "agent": 2,
    "call": 3,
    "call_end": 4,
    "agent_end": 5,
}


# ---------------------------------------------------------------------------
# Annotation collection — L2/L3 XAI context attached to hover text
# ---------------------------------------------------------------------------

_ANNOTATION_KINDS: frozenset[str] = frozenset(
    {
        "routing",
        "routing_result",
        "context_assembled",
        "state_update_start",
        "state_update_end",
        "agent_communication_start",
        "agent_communication_end",
    }
)

_ANNOTATION_LABEL: dict[str, str] = {
    "routing": "→ routing",
    "routing_result": "→ routed",
    "context_assembled": "ctx",
    "state_update_start": "state↑",
    "state_update_end": "state↓",
    "agent_communication_start": "agent-remote↑",
    "agent_communication_end": "agent-remote↓",
}


def _collect_annotations(
    events: list[dict],
    records: list[dict],
) -> dict[str, list[str]]:
    """Return ``call_id \u2192 [annotation_summary_line, ...]`` for L2/L3 XAI hover enrichment.

    Annotation events are matched to the call record they annotate by a real
    id, never timestamp containment. Two shapes, both already resolved by the
    runtime (see ObservabilityOperator._resolve_transition_ids):

    - governance_authorize/validate_*, obs_wrap_gov_*, and context_assembled
      are recorded AT the call they annotate \u2014 the event's own ``call_id`` IS
      that call's ``call_id``.
    - state_update_start/end is a CHILD of the call whose result it records
      (record_context_mutation's op threading) \u2014 its own ``call_id`` is a
      distinct, self-paired synthetic id, so the real link is its
      ``parent_call_id``.

    Events with neither a matching ``call_id`` nor ``parent_call_id`` (e.g.
    turn/session-scoped state_update, or an annotation kind with no producer
    at all \u2014 routing/agent_communication today) are silently skipped: there is
    no real id to attach them to, and none is guessed.
    """
    ann_events = [e for e in events if e.get("kind") in _ANNOTATION_KINDS]
    if not ann_events:
        return {}

    rec_by_id = {r["call_id"]: r for r in records}
    result: dict[str, list[str]] = defaultdict(list)

    for ann in ann_events:
        kind = ann.get("kind", "")

        _own_id = ann.get("call_id")
        best_rec: Optional[dict] = rec_by_id.get(_own_id) if _own_id in rec_by_id else None
        if best_rec is None:
            _parent_id = ann.get("parent_call_id")
            best_rec = rec_by_id.get(_parent_id) if _parent_id else None

        if best_rec is None:
            continue

        label = _ANNOTATION_LABEL.get(kind, kind)
        parts = [label]
        # Extract the most meaningful payload field for the short summary
        for _f in ("target_agent_id", "target", "to", "task"):
            val = ann.get(_f)
            if val:
                parts.append(str(val))
                break
        if kind == "context_assembled":
            segs = ann.get("segments")
            tokens = ann.get("total_tokens")
            if segs is not None:
                parts.append(f"{segs} seg")
            if tokens is not None:
                parts.append(f"{tokens} tok")
        if kind in ("state_update_start", "state_update_end"):
            action = ann.get("update_type")
            if action:
                parts.append(str(action))
            preview = ann.get("content_preview")
            if preview:
                parts.append(str(preview)[:40])
        result[best_rec["call_id"]].append(" ".join(parts))

    return dict(result)


# ── Mechanism colour badges ──────────────────────────────────────────────

_MECH_BADGE: dict[str, str] = {
    "inject": "🔵",
    "rag": "🟣",
    "tool_call": "🟠",
}

_CAUSE_BADGE_MAP: dict[str, str] = {
    "deterministic": "⚙",
    "stochastic": "🎲",
    "explicit": "👤",
}


def _source_category(source: str, mechanism: str = "inject") -> str:
    """Derive a human-readable category from a CPR source name.

    Category is determined by the **source** (where the data comes from),
    not the mechanism (how it was retrieved).  RAG is a mechanism, not a source.
    """
    sl = source.lower()
    if sl.startswith("memory:") or sl.startswith("mem:"):
        return "MEMORY"
    if "skill" in sl or sl.startswith("facet:skill"):
        return "SKILL"
    if sl.startswith("context/role"):
        return "SYSTEM"
    if sl.startswith("context/intent"):
        return "SYSTEM"
    if sl.startswith("context/"):
        return "SYSTEM"
    if sl.startswith("tool:") or sl.startswith("tool_result"):
        return "TOOL"
    if mechanism == "tool_call":
        return "TOOL"
    return "CONTEXT"


_SECTION_ORDINAL_RE = re.compile(r"assembled/(\d+)/")


_ROLE_SEMANTICS = {
    "system": dict(
        cause="system prompt",
        causeType="system",
        trigger="system_initialization",
        actor="system",
        via="system_configuration",
        cause_detail="system prompt",
    ),
    "user": dict(
        cause="user turn",
        causeType="user_provided",
        trigger="user_input",
        actor="user",
        via="user_provided",
        cause_detail="user turn",
    ),
    "assistant": dict(
        cause="LLM response",
        causeType="llm_decision",
        trigger="llm_response",
        actor="llm",
        via="llm_generated",
        cause_detail="assistant turn",
    ),
    "tool": dict(
        cause="tool result",
        causeType="tool_result",
        trigger="tool_execution",
        actor="tool",
        via="tool_provided",
        cause_detail="tool execution result",
    ),
}

_ROLE_SEMANTICS_ASSISTANT_TOOL_CALL = dict(
    cause="LLM tool-call decision",
    causeType="llm_decision",
    trigger="llm_tool_call",
    actor="llm",
    via="llm_generated",
    cause_detail="LLM tool-call decision",
)

_ROLE_SEMANTICS_TOOL_DELEGATION = dict(
    cause="delegation result",
    causeType="delegated",
    trigger="delegation_result",
    actor="delegated_agent",
    via="delegation",
    cause_detail="delegation result",
)

_UNKNOWN_SEMANTICS = dict(
    cause="?",
    causeType="unknown",
    trigger="context_addition",
    actor="?",
    via="injected",
    cause_detail="context addition",
)


def _detect_delegation(tool_call_id: str, content: str) -> bool:
    """Check if tool result is from delegation."""
    return "delegate" in content.lower() or "delegat" in tool_call_id.lower()


def _derive_context_semantics(role: str, source: str = "", *, tool_call_id: str = "", content: str = "") -> dict:
    """Derive semantic fields from role, source, tool_call_id, and content.

    This implements the normalized data model where events.jsonl carries only
    source facts (role, source, IDs) and semantics are derived at query time.
    The derivation rule is deterministic: f(role, source, ...) → semantics dict.

    Args:
        role: OpenAI message role (system, user, assistant, tool)
        source: Provenance source path (e.g., "context/system", "assembled/user")
        tool_call_id: Tool call identifier (for delegation detection)
        content: Message content (for delegation detection)

    Returns:
        dict with keys: cause, causeType, trigger, actor, via, cause_detail
    """
    # Normalize inputs
    r = str(role or "?").strip().lower() if role else ""
    s = str(source or "").strip().lower() if source else ""

    # Lookup base semantics for role
    semantics = _ROLE_SEMANTICS.get(r)
    if semantics is None:
        return _UNKNOWN_SEMANTICS.copy()

    # Apply role-specific overrides for special cases
    if r == "assistant" and "tool_call" in s:
        return _ROLE_SEMANTICS_ASSISTANT_TOOL_CALL.copy()
    elif r == "tool" and _detect_delegation(tool_call_id, content):
        return _ROLE_SEMANTICS_TOOL_DELEGATION.copy()

    return semantics.copy()


def _collect_context_provenance(
    events: list[dict],
    records: list[dict],
) -> dict[str, list[dict]]:
    """Return ``call_id → [context_part_event, …]`` for context provenance hover enrichment.

    This function collects ``context_part_contributed`` events (the authoritative
    source for per-chunk provenance information) and enriches them for visualization.

    Context Metadata Model:
    - **Normalized storage**: events.jsonl carries only source facts (role, source, IDs)
    - **Derived semantics**: actor, trigger, via are computed deterministically at query time
    - **Deterministic rule**: f(role, source) → {actor, trigger, via}
    - **Example**: role="assistant" + source="assembled/tool_call" → actor="llm", trigger="llm_tool_call"

    ``context_part_contributed`` events are matched to LLM call records by a
    direct, real id match on ``llm_call_id`` — the runtime resolves this to the
    SAME call_id the LLM call's own llm_call_start/end use via runtime-determined
    resolution.

    ``context_part_contributed`` is the authoritative source for per-chunk
    PROVENANCE (source, mechanism, retention decision, section ordinal,
    token estimate), carrying a ``content_preview`` hard-truncated to 120 chars. The sibling
    ``context_assembled`` event for the SAME call_id/llm_call_id is
    the authoritative source for the assembled context's full, non-truncated
    ``messages`` — so each part's preview is upgraded here to the matching
    full message text (matched by the part's ``section_id`` ordinal, e.g.
    ``"assembled/0/system"`` → ``messages[0]``) whenever that fuller text is
    available, without mutating the original trace event.
    """
    cpr_events = [e for e in events if e.get("kind") == "context_part_contributed"]
    if not cpr_events:
        return {}

    llm_records_by_id = {r["call_id"]: r for r in records if r.get("call_type") == "LLMCall"}

    ca_messages_by_call: dict[str, list] = {}
    for _e in events:
        if _e.get("kind") == "context_assembled" and _e.get("messages"):
            _ca_cid = _e.get("call_id") or _e.get("llm_call_id")
            if _ca_cid:
                ca_messages_by_call[_ca_cid] = _e["messages"]

    result: dict[str, list[dict]] = defaultdict(list)

    for ev in cpr_events:
        raw_cid = ev.get("llm_call_id") or ""
        matched = llm_records_by_id.get(raw_cid) if raw_cid else None
        if not matched:
            continue
        messages = ca_messages_by_call.get(raw_cid)
        if messages and not ev.get("content"):
            _m = _SECTION_ORDINAL_RE.match(str(ev.get("section_id") or ""))
            if _m:
                _idx = int(_m.group(1))
                if 0 <= _idx < len(messages):
                    _full = messages[_idx].get("content")
                    if _full and isinstance(_full, str):
                        ev = {**ev, "content": _full}
        result[matched["call_id"]].append(ev)

    return dict(result)


def _collect_llm_tool_decisions(
    events: list[dict],
    records: list[dict],
) -> dict[str, str]:
    """Return ``call_id → decision text`` for LLMCall records whose real
    ``output`` is a tool-call decision, not text.

    ``llm_call_end.output`` is legitimately empty whenever the LLM's turn
    ended by deciding to call a tool (single or parallel/delegation) — this
    is not missing telemetry, the native trace already says so explicitly
    via ``llm_call_end.next_step`` (``"TOOL_CALL"`` / ``"PARALLEL_TOOL_CALLS"``),
    a field dag.py never previously read. Current traces also carry the
    decision as ``llm_call_end.tool_calls``; this reconstruction is what reads
    traces written before that field. There the decision (tool name +
    arguments) is not repeated on llm_call_end; it is the very next
    record sharing this LLM call's own ``parent_call_id`` (its agent's
    execution scope) — verified against a real production trace to hold for
    every TOOL_CALL/PARALLEL_TOOL_CALLS occurrence, including delegation
    (``delegate_to_<agent>`` surfaces as a normal ``ToolCall`` record, not an
    ``AgentCall``):
      - ``next_step == "TOOL_CALL"``      → the next ``ToolCall`` record.
      - ``next_step == "PARALLEL_TOOL_CALLS"`` → the next ``ProcessingCall``
        record with ``processing_type == "parallel_group"``, whose ``tools``
        list already carries every parallel decision's name + arguments.
    """
    result: dict[str, str] = {}
    needs_decision = [
        r
        for r in records
        if r.get("call_type") == "LLMCall"
        and not str(r.get("output") or "").strip()
        and r.get("next_step") in ("TOOL_CALL", "PARALLEL_TOOL_CALLS")
    ]
    if not needs_decision:
        return result

    by_parent: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        pid = r.get("parent_call_id")
        if pid:
            by_parent[pid].append(r)
    for _siblings in by_parent.values():
        _siblings.sort(key=lambda r: r["start_ts"])

    for rec in needs_decision:
        pid = rec.get("parent_call_id")
        if not pid:
            continue
        _next_step = rec.get("next_step")
        for sib in by_parent.get(pid, []):
            if sib is rec or sib["start_ts"] < rec["end_ts"]:
                continue
            if _next_step == "TOOL_CALL" and sib.get("call_type") == "ToolCall":
                result[rec["call_id"]] = "[Tool call] " + str(sib.get("input") or "")
                break
            if (
                _next_step == "PARALLEL_TOOL_CALLS"
                and sib.get("call_type") == "ProcessingCall"
                and sib.get("processing_type") == "parallel_group"
            ):
                _lines: list[str] = []
                for _t in sib.get("tools") or []:
                    if not isinstance(_t, dict):
                        continue
                    _tn = _t.get("tool_name") or "tool"
                    _args = _t.get("arguments")
                    if isinstance(_args, dict):
                        _argstr = ", ".join(f"{k}={v!r}" for k, v in _args.items())
                    else:
                        _argstr = str(_args or "")
                    _lines.append(f"[Tool call] → {_tn}({_argstr})")
                if _lines:
                    result[rec["call_id"]] = "\n".join(_lines)
                break

    return result


def _format_cpr_hover(cpr_parts: list[dict]) -> str:
    """Format context provenance parts into a readable hover-text block.

    Shows each context part with its source, mechanism badge, and the actual
    content text (up to 300 chars per part).
    """
    if not cpr_parts:
        return ""

    lines: list[str] = ["\n\n📦 Context Assembly:"]
    total_tokens = 0
    for part in cpr_parts:
        source = part.get("source", "?")
        mechanism = part.get("mechanism", "inject")
        cause_type = part.get("cause_type", "deterministic")
        tokens = part.get("token_estimate", 0)
        retained = part.get("retained", True)
        content = part.get("content") or part.get("content_preview") or ""
        placement = part.get("placement", "")
        mech_badge = _MECH_BADGE.get(mechanism, "⚪")
        cause_badge = _CAUSE_BADGE_MAP.get(cause_type, "")

        # Source category for clearer labeling
        cat = _source_category(source, mechanism)
        status = "" if retained else " ❌evicted"

        lines.append(f"\n  {mech_badge} [{cat}] {source} {cause_badge} {tokens}tok{status}")
        if placement:
            lines.append(f"     placement: {placement}")

        # Show actual content — up to 300 chars
        if content:
            preview = content[:300].replace("\n", "\n     ")
            if len(content) > 300:
                preview += "…"
            lines.append(f"     ───\n     {preview}")
        else:
            lines.append("     (no content captured)")

        total_tokens += tokens

    lines.append(f"\n  ── {len(cpr_parts)} parts · {total_tokens} tokens total")
    return "\n".join(lines)


def _format_context_assembly_diff(messages: list[dict]) -> str:
    """Format a context-assembly step as a small diff-style hover block.

    The native trace already tells us which messages were assembled. We turn
    that into a concise "what was added" view instead of repeating raw
    input/output text.
    """
    if not messages:
        return ""

    lines: list[str] = ["\n\n📦 Context Diff:"]
    added_parts = 0
    for message in messages:
        role = str(message.get("role") or "?").strip().lower()
        from mas.runtime.contracts.content_types import text_of

        content = text_of(message.get("content"), media="placeholder").strip()
        if not content:
            continue

        badge = "PREPEND" if role == "system" else "APPEND"
        desc = {
            "system": "system prompt",
            "user": "user turn",
            "assistant": "assistant turn",
            "tool": "tool result",
        }.get(role, f"{role} chunk")
        preview = content[:300].replace("\n", "\n     ")
        if len(content) > 300:
            preview += "…"
        lines.append(f"\n  [{badge}] {desc}")
        lines.append(f"     ───\n     {preview}")
        added_parts += 1

    if not added_parts:
        return ""

    lines.append(f"\n  ── {added_parts} part{'s' if added_parts != 1 else ''} added")
    return "\n".join(lines)


def _stagger_coinc_processing_calls(seq: list[dict]) -> list[dict]:
    """Stagger coincident point-in-time ProcessingCall records with small offsets.

    When the runtime emits multiple per-actor ProcessingCall spans at the same
    wall-clock instant (start_ts == end_ts == ts_now), they would share the
    same StateNode boundaries and render as overlapping bars.  This helper
    gives each coincident ProcessingCall a tiny sequential offset so that each
    occupies its own visual slot in the Calls lane:

        PC1: (ts, ts+δ), PC2: (ts+δ, ts+2δ), …

    The stagger increment (1ms) is well below human perception but large
    enough to create distinct StateNode keys.
    """
    if not seq:
        return seq
    result: list[dict] = []
    i = 0
    while i < len(seq):
        crec = seq[i]
        # Only stagger point-in-time ProcessingCalls (start ≈ end). Native
        # parallel_group fork/join markers (`_pg_fork_marker`/`_pg_join_marker`,
        # see tree.py's `_make_call_sequence`) are excluded even though they're
        # also zero-duration ProcessingCalls: their own ts is deliberately
        # exact (chosen so dag.py can detect an exact-ts collision with the
        # preceding/following state and merge onto it instead of minting a
        # redundant box — see dag.py's `_pg_marker_is_reuse`). `_TS_TOL`
        # (~50ms) is far too coarse to tell "genuinely the same instant" from
        # "a different real event a couple ms away" within one native
        # parallel_group (real gaps there are routinely sub-millisecond) —
        # bundling a fork/join marker into a stagger group with some other
        # coincidentally-nearby ProcessingCall silently widened its end_ts by
        # a stagger tick, breaking the start_ts==end_ts invariant the merge
        # logic depends on (regression: join's own is_join tag ended up on an
        # orphaned StateNode that was never appended to the lane at all).
        if (
            crec.get("call_type") == "ProcessingCall"
            and abs(crec["end_ts"] - crec["start_ts"]) <= _TS_TOL
            and not crec.get("_pg_fork_marker")
            and not crec.get("_pg_join_marker")
        ):
            ts = crec["start_ts"]
            group: list[dict] = [crec]
            j = i + 1
            while j < len(seq):
                nxt = seq[j]
                if (
                    nxt.get("call_type") == "ProcessingCall"
                    and abs(nxt["start_ts"] - ts) <= _TS_TOL
                    and abs(nxt["end_ts"] - ts) <= _TS_TOL
                    and not nxt.get("_pg_fork_marker")
                    and not nxt.get("_pg_join_marker")
                ):
                    group.append(nxt)
                    j += 1
                else:
                    break
            # A lone point-in-time ProcessingCall (e.g. a synthesized context
            # -assembly node before an LLM call) needs no staggering — leave its
            # boundaries intact so it keeps sharing the state with the following
            # call.  Rewriting its end to ts+δ here would break that shared
            # boundary and open an empty connector gap before the LLM.
            if len(group) < 2:
                result.append(crec)
                i += 1
                continue
            # Stagger each member with a small time offset.
            for idx, rec in enumerate(group):
                staggered = dict(rec)
                staggered["start_ts"] = ts + idx * _STAGGER_DUR
                staggered["end_ts"] = ts + (idx + 1) * _STAGGER_DUR
                result.append(staggered)
            # Snap the next record's start_ts forward if it falls inside the
            # staggered group.  Only advance — never move it backward — and
            # shift end_ts by the same delta so a real (non-zero) duration is
            # never corrupted (e.g. shrunk, or inverted into end_ts <
            # start_ts) — the same rule _reserve_marker_width in tree.py
            # applies for its own analogous forward-bump.
            _group_end = ts + len(group) * _STAGGER_DUR
            if j < len(seq):
                nxt_copy = dict(seq[j])
                _orig_end = nxt_copy.get("end_ts", _group_end)
                _delta = _group_end - nxt_copy.get("start_ts", _group_end)
                if _delta > 0:
                    nxt_copy["start_ts"] = _group_end
                    nxt_copy["end_ts"] = _orig_end + _delta
                result.append(nxt_copy)
                i = j + 1
                # `nxt_copy`'s own end just moved forward by `_delta`, but any
                # record still exactly touching its *original* end (the
                # no-gap sibling boundary `_align_record_boundaries` already
                # set up) was left behind at that stale timestamp — same
                # class of bug `_reserve_marker_width` guards against in
                # tree.py for its own analogous forward-bump. Cascade the
                # same delta through every consecutive record still exactly
                # touching the previous one's pre-shift end.
                if _delta > 0:
                    boundary = _orig_end
                    k = i
                    while k < len(seq) and abs(seq[k]["start_ts"] - boundary) <= 1e-6:
                        nxt2 = seq[k]
                        boundary = nxt2["end_ts"]
                        seq[k] = {
                            **nxt2,
                            "start_ts": nxt2["start_ts"] + _delta,
                            "end_ts": nxt2["end_ts"] + _delta,
                        }
                        k += 1
            else:
                i = j
        else:
            result.append(crec)
            i += 1
    return result
