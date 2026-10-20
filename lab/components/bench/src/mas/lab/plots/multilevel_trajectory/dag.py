#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""DAG assembly: sequences → StateNode registry + LaneDef list."""

from collections import defaultdict
from dataclasses import replace
from typing import Optional

from mas.lab.plots._trajectory_validator import validate_trajectory_dag
from mas.lab.plots.multilevel_trajectory.annotations import (
    _HOVER_PRIORITY,
    _collect_annotations,
    _collect_context_provenance,
    _collect_llm_tool_decisions,
    _format_cpr_hover,
    _source_category,
    _derive_context_semantics,
    _stagger_coinc_processing_calls,
)
from mas.lab.plots.multilevel_trajectory.constants import (
    _PROC_TYPE_LABEL,
    _STAGGER_DUR,
    _TS_TOL,
    TYPE_LABEL,
    PROCESSING_TYPE_WAIT_STATE,
    PROCESSING_TYPE_CONTEXT_ASSEMBLY,
    PROCESSING_TYPE_PARALLEL_GROUP,
    PROCESSING_TYPE_PARALLEL_FORK,
    PROCESSING_NAME_PARALLEL_FORK,
    PROCESSING_NAME_CONTEXT_ASSEMBLY,
    WAIT_SCOPE_DELEGATION,
)
from mas.lab.plots.multilevel_trajectory.governance import (
    _collect_blocked_actions,
    _collect_governance_decisions,
    _collect_hitl_exchanges,
    _collect_retry_chains,
    notable_governance,
)
from mas.lab.plots.multilevel_trajectory.models import LaneDef, StateNode, TransNode
from mas.lab.plots.multilevel_trajectory.records import (
    _extract_final_output,
    _extract_user_input,
    _synthesize_thinking_records,
)
from mas.lab.plots.multilevel_trajectory.tree import (
    _align_record_boundaries,
    _assign_dfs_positions,
    _build_call_tree,
    _detect_delegation_forks,
    _make_agent_sequence,
    _make_call_sequence,
    _native_parallel_group_by_tool_call_id,
    _reset_branch_agent_call_ids,
)

#: Every optional annotation layer _build_dag can attach on top of the core
#: call-tree structure. Each is a pure function over (events, records) — see
#: governance.py's module docstring — so disabling one just skips computing
#: and attaching that data; nothing else in the DAG depends on it.
_ALL_FACETS = frozenset({"cpr", "governance", "annotations", "thinking"})


def _mas_lane_span(
    start_ts: float,
    end_ts: float,
    prev_end: Optional[float],
) -> tuple[float, float]:
    """Keep one MAS bar from starting before the previous bar ended."""
    if prev_end is not None and start_ts < prev_end:
        return prev_end, max(end_ts, prev_end)
    return start_ts, end_ts


def _build_dag(
    records: list[dict],
    events:  list[dict],
    show_provenance: bool = True,
    enabled_facets: "set[str] | None" = None,
) -> tuple[dict[float, StateNode], list[LaneDef]]:
    """Assemble the DAG from the call tree.

    ``enabled_facets`` toggles the optional annotation layers overlaid on the
    core structural DAG (``_ALL_FACETS``); ``None`` (default) enables all of
    them. ``show_provenance=False`` is sugar for excluding ``"cpr"`` — kept
    for backward compatibility with existing callers.

    Returns
    -------
    state_reg : {ts: StateNode}     — shared state registry
    lanes     : [LaneDef, …]       — ordered swim-lane sequences

    Each lane's ``sequence`` is a strict alternation::

        [StateNode, TransNode, StateNode, TransNode, …, StateNode]

    States at the same ``ts`` in different lanes are the same object
    (shared reference), which the renderer uses to draw multi-lane connectors.
    """
    facets = set(enabled_facets) if enabled_facets is not None else set(_ALL_FACETS)
    if not show_provenance:
        facets.discard("cpr")

    # Keep the original event-derived boundaries for retry-chain detection:
    # _align_record_boundaries can snap adjacent spans to shared boundaries,
    # which is correct for rendering but can swallow the small gap that
    # carries an ingress RETRY decision between two attempts.
    _records_pre_align = [dict(r) for r in records]

    # Build the call tree and structurally align parent/child boundaries
    # FIRST — this can nudge a root record's start/end (envelope pass, see
    # _align_record_boundaries) — so t_min/t_max below are derived from the
    # FINAL boundaries. Deriving them before alignment left the session/MAS/
    # Agents lanes' synthetic exit state pinned to a stale, pre-alignment
    # t_max while the Calls lane's own last transition (built from the
    # post-alignment record) ended a fraction of a second later — two
    # almost-identical but distinct final states instead of one shared
    # boundary, which rendered as the trace never "returning to the user".
    children_of, parent_of = _build_call_tree(records)
    _align_record_boundaries(records, children_of)

    all_ts = [float(e.get("timestamp") or 0) for e in events if e.get("timestamp")]
    # Use record start/end times as the authoritative bounds — they include
    # synthetic end_ts (+1 s) for calls whose *_end event was missing,
    # which is always the correct diagram extent.  Raw event timestamps are
    # only a fallback when no records were produced.
    if records:
        t_min = min(r["start_ts"] for r in records)
        t_max = max(r["end_ts"]   for r in records)
    else:
        t_min = min(all_ts) if all_ts else 0.0
        t_max = max(all_ts) if all_ts else 1.0
    if t_max <= t_min:
        t_max = t_min + 1.0

    state_reg: dict[float, StateNode] = {}
    hover_pri: dict[float, int]       = {}

    # L2/L3 XAI annotation map: call_id → list of short summary strings
    ann_map: dict[str, list[str]] = (
        _collect_annotations(events, records) if "annotations" in facets else {}
    )

    # L4 context provenance: context_part_contributed → per-call_id summary
    cpr_map: dict[str, list[dict]] = (
        _collect_context_provenance(events, records) if "cpr" in facets else {}
    )

    # LLM calls whose real "output" was a tool-call decision (llm_call_end.
    # next_step == TOOL_CALL/PARALLEL_TOOL_CALLS) but whose own output field
    # is empty — synthesize the decision from the next ToolCall/parallel_group
    # record so the transition's hoverOut shows it natively instead of a
    # client-side reconstruction.
    llm_tool_decisions: dict[str, str] = _collect_llm_tool_decisions(events, records)

    # Governance facet: decisions, HITL Q&A, blocked-action ghosts, retries.
    # See governance.py's module docstring for why this operates on the same
    # (events, records) pair the KG adapter already produces.
    if "governance" in facets:
        gov_map:    dict[str, list[dict]] = _collect_governance_decisions(events, records)
        hitl_map:   dict = _collect_hitl_exchanges(events)
        blocked:    list[dict] = _collect_blocked_actions(events, records)
        retry_map:  dict[str, dict] = _collect_retry_chains(events, _records_pre_align)
    else:
        gov_map, hitl_map, blocked, retry_map = {}, {}, [], {}

    def state(ts: float, hover: str = "", level: str = "") -> StateNode:
        """Fetch or create the StateNode at *ts* (exact timestamp key).

        Hover text is overwritten only by a level with equal or higher
        priority (call > agent > mas > session). The session entry/exit
        boundary states are the one exception: once flagged as the actual
        user-input/user-output anchor (``is_user_entry``/``is_user_exit``)
        AND already carrying real extracted text, that hover is never
        overwritten by an unrelated record that happens to share the exact
        same wall-clock ts (common — the very first/last call in the trace
        frequently starts/ends exactly at the session boundary) — otherwise
        the real user text is silently replaced by an unrelated internal
        call's technical label (e.g. "context_assembly"). If the session-level
        extraction found nothing (empty hover — native telemetry sometimes
        carries no execution_end output), the normal priority fallback still
        applies so the boundary state isn't left blank.
        """
        pri = _HOVER_PRIORITY.get(level, -1)
        if ts not in state_reg:
            state_reg[ts] = StateNode(ts=ts, hover=hover)
            hover_pri[ts] = pri if hover else -1
        elif hover and pri >= hover_pri.get(ts, -1):
            _existing = state_reg[ts]
            if not ((_existing.is_user_entry or _existing.is_user_exit) and _existing.hover):
                _existing.hover = hover
                hover_pri[ts]   = pri
        return state_reg[ts]

    s_in  = _extract_user_input(events)
    s_out = _extract_final_output(events)

    rec_by_id: dict[str, dict] = {r["call_id"]: r for r in records}
    mas_records    = sorted([r for r in records if r["level"] == "mas"],
                            key=lambda r: r["start_ts"])
    agent_sequence = _make_agent_sequence(records, children_of, parent_of)

    # Fork/Branch detection (tree-structural — see _detect_delegation_forks):
    # computed before _make_call_sequence so it can tag each fork's 2nd..Nth
    # sibling's own first call as a branch entry (see
    # _reset_branch_agent_call_ids / _make_call_sequence's docstring).
    fork_groups = _detect_delegation_forks(records, parent_of, rec_by_id)
    _reset_branch_ids = _reset_branch_agent_call_ids(fork_groups)
    call_sequence = _stagger_coinc_processing_calls(
        _make_call_sequence(agent_sequence, children_of, _reset_branch_ids)
    )
    # agent_sequence's own start_ts/end_ts (used below to build the Agents
    # lane) were fixed by _make_agent_sequence BEFORE call_sequence's own
    # coincident-ProcessingCall staggering ran. Staggering (and the
    # marker-width reservation nested inside _make_call_sequence) only ever
    # pushes timestamps FORWARD to resolve a same-instant collision — e.g. a
    # delegate's own wait_state RESUME record and the delegating agent's own
    # context-assembly step landing on the exact same aligned instant get
    # fanned out a millisecond apart — but that forward push can cascade
    # through every later touching sibling boundary (_align_record_
    # boundaries' Rule 2 makes every sibling share an exact boundary with
    # the next), including the delegate's own real last call. Left
    # unreconciled, the Agents lane's fragment boundary (this agent's own
    # end_ts) stays a hair BEHIND the Calls lane's actual (post-stagger)
    # last real content state for that same agent — the two lanes disagree
    # about when the fragment truly ends: the collapsible "agent run" group
    # (see multilevel.html's _computeCollapsibleGroups) is built from the
    # Agents lane's boundary, so a Calls-lane transition ending a hair past
    # it fails the group's containment check and renders OUTSIDE the
    # collapsed group instead of being hidden with the rest of that agent's
    # own calls — and the two lanes' shared-boundary state (see `state()`
    # above) lands in two different chart columns instead of one, breaking
    # cross-lane alignment. Reconcile every fragment's end_ts to the actual
    # (post-stagger) max end_ts of its own tagged call_sequence records —
    # only ever widening, matching _align_record_boundaries' own "envelope,
    # never shrink a real timestamp" rule. Delegation markers and wait_state
    # ProcessingCalls are pure control-flow bookkeeping, not this agent's own
    # content — each already gets its own dedicated boundary handling further
    # below (the WAIT/RESUME state-pairing block, _snap_to_agent_lane_ts,
    # etc.), which reads THIS fragment's un-widened end_ts as one of its own
    # inputs. Including them here double-counts their own stagger/reserve-
    # width shift on top of that already-correct handling (observed: folding
    # a wait_state record's own +1ms reservation into the Agents lane's own
    # boundary shifted a later WAIT/RESUME cluster's snap target and silently
    # dropped that state's CPR popup content).
    _frag_end_ts: dict[int, float] = {}
    for _crec in call_sequence:
        _fidx = _crec.get("_agent_fragment_idx")
        if _fidx is None:
            continue
        if _crec.get("_delegation_marker") or str(_crec.get("processing_type") or "") == PROCESSING_TYPE_WAIT_STATE:
            continue
        _cend = _crec.get("end_ts", 0.0)
        if _cend > _frag_end_ts.get(_fidx, float("-inf")):
            _frag_end_ts[_fidx] = _cend
    for _fidx in sorted(_frag_end_ts):
        _max_end = _frag_end_ts[_fidx]
        _arec = agent_sequence[_fidx]
        _delta = _max_end - _arec.get("end_ts", 0.0)
        # Cap the widening at _TS_TOL: the legitimate case this reconciles
        # is a "same logical instant, independent event sources" stagger of
        # at most a few _STAGGER_DUR ticks (see _stagger_coinc_processing_
        # calls). _stagger_coinc_processing_calls' cascade can — separately,
        # and out of scope here — shift a record forward by a much larger,
        # structurally wrong amount when an unrelated fork sibling's own
        # boundary coincides with this one (observed: a fork branch's real
        # ~3.6s-long call stretched to ~4.7s longer than it really ran).
        # Trusting an arbitrarily large widening here would let that
        # separate, pre-existing bug corrupt the Agents lane's own fragment
        # boundary too — worse than leaving it at its own (merely stale-by-
        # a-hair) original value.
        if 0 < _delta <= _TS_TOL:
            # A fresh dict, never an in-place mutation: some fragments (the
            # common, non-split case) reuse the SAME dict object as the
            # original record in `records`/`rec_by_id` (see
            # _make_agent_sequence's `result.append(rec)`) — mutating it here
            # would silently change what every OTHER already-computed
            # reference to that record sees too (fork detection, wait-state
            # lookups, governance annotations, all computed above from the
            # same `records` list), an aliasing surprise unrelated to this
            # fragment's own boundary fix.
            agent_sequence[_fidx] = {**_arec, "end_ts": _max_end}

    # A RESUME wait_state record (unlike WAIT — see the exclusion above) is
    # the delegating agent's own re-entry, immediately following the
    # delegate's own just-reconciled fragment: its raw start_ts can be a
    # hair BEHIND that fragment's real content end for the exact same
    # "independent event sources" reason (it's minted by native
    # observability's own wait-state bookkeeping, not by whatever real
    # LLMCall/ToolCall the delegate's own subtree actually ended on). Left
    # unreconciled, the RESUME state renders at a distinct, slightly EARLIER
    # ts than the delegate's own now-widened fragment end — the exact
    # S9/R1-vertical-misalignment symptom this whole reconciliation exists
    # to fix. Only ever advances (never shrinks), same cap as above.
    for _ci, _crec in enumerate(call_sequence):
        if str(_crec.get("processing_type") or "") != PROCESSING_TYPE_WAIT_STATE:
            continue
        if str(_crec.get("wait_role") or "").strip().upper() != "RESUME":
            continue
        _fidx = _crec.get("_agent_fragment_idx")
        if _fidx is None or _fidx - 1 not in _frag_end_ts:
            continue
        _prev_end = _frag_end_ts[_fidx - 1]
        _delta = _prev_end - _crec.get("start_ts", 0.0)
        if 0 < _delta <= _TS_TOL:
            call_sequence[_ci] = {
                **_crec,
                "start_ts": _crec.get("start_ts", 0.0) + _delta,
                "end_ts": _crec.get("end_ts", 0.0) + _delta,
            }


    # Staggering coincident point-in-time ProcessingCalls (above, inside
    # _stagger_coinc_processing_calls) fans out records that share the exact
    # same ts so they don't visually overlap in the Calls lane — pushing the
    # LAST member of such a group a hair past t_max when the group sits
    # exactly at the session's final timestamp. Re-derive that precise delta
    # here (mirroring the same grouping condition) from `records` directly —
    # NOT from `call_sequence`, whose entries can carry unrelated
    # marker-width-reservation padding (tree.py) that has nothing to do with
    # a real wall-clock boundary and must never leak into t_max. Otherwise
    # the Session/MAS/Agents lanes' exit state stays pinned to the stale
    # t_max while the Calls lane's real final bar ends a fraction of a
    # second later — the trace never visibly "returns to the user".
    _tail_coinc_group_size = sum(
        1 for r in records
        if r.get("call_type") == "ProcessingCall"
        and abs(r["end_ts"] - r["start_ts"]) <= _TS_TOL
        and abs(r["start_ts"] - t_max) <= _TS_TOL
    )
    if _tail_coinc_group_size >= 2:
        t_max += _tail_coinc_group_size * _STAGGER_DUR

    entry = state(t_min, s_in,  "session")
    exit_ = state(t_max, s_out, "session")
    entry.is_user_entry = True
    exit_.is_user_exit  = True
    # The user's own prompt is a real fact known since the very first state
    # (this one) — attach it as a genuine CPR part now (same source key
    # ``assembled/user`` a native ``context_part_contributed`` event uses
    # for this same text), not left for the first LLM call's own context
    # assembly to introduce later. Without this, the popup at S1 falls back
    # to a generic "(no cprData)" wrap, and the SAME prompt reappearing at
    # the first LLM's own native cprData gets wrongly flagged "NEW" there —
    # it was never new, it's the one fact present since session start.
    if s_in and not entry.cpr_data:
        entry.cpr_data = [{
            "source": "assembled/user",
            "category": _source_category("assembled/user", "inject"),
            "mechanism": "inject",
            "retrieval": "",
            "decision": "",
            "causeType": "deterministic",
            "cause": "user",
            "tokens": max(1, len(s_in) // 4),
            "retained": True,
            "placement": "assembled/user",
            "content": s_in,
        }]

    # DFS virtual-position axis: call_sequence is already DFS-ordered (no
    # wall-clock sort) and already tagged, so walking it once assigns every
    # boundary timestamp a monotonic position, with a fixed-tick reset
    # (instead of the real, often-negative gap) at each branch entry.
    _ts_to_dfs_pos, _reset_branch_id = _assign_dfs_positions(call_sequence)

    # Extend the DFS axis to each fork branch's OWN raw start_ts too — the
    # Agents lane's true fragment boundary (see _make_agent_sequence) is NOT
    # the same timestamp as the branch's first call-level record tagged
    # _branch_entry in call_sequence (_reserve_marker_width pushes that one
    # later to clear the delegating marker cluster). _assign_dfs_positions
    # above only covers call_sequence's own timestamps, so without this the
    # generic real-time interpolation fallback below (which assumes nearby
    # timestamps are nearby in DFS order — false for a fork's 2nd..Nth
    # sibling, dispatched long before DFS reaches its subtree) would place
    # this raw boundary in the middle of an already-passed DFS range instead
    # of just before the branch's own tagged entry. Every rank>0 fork member
    # gets its raw start_ts pinned to a hair before its own _branch_entry
    # position — both the Agents lane (_bridge_to_state, below) and the Calls
    # lane's own "inject agent-boundary timestamps" pass rely on this.
    _reset_ts_by_branch_id: dict[str, float] = {
        _bid: _ts for _ts, _bid in _reset_branch_id.items()
    }
    for _members in fork_groups.values():
        for _member in _members[1:]:
            _raw_ts   = _member["start_ts"]
            _entry_ts = _reset_ts_by_branch_id.get(_member["call_id"])
            if _entry_ts is not None and _raw_ts not in _ts_to_dfs_pos:
                _ts_to_dfs_pos[_raw_ts] = _ts_to_dfs_pos[_entry_ts] - 1e-3

    seq_ctr = [0]

    def next_seq() -> int:
        seq_ctr[0] += 1
        return seq_ctr[0]

    def _bridge_to_state(
        elems: list, target_ts: float, hover: str = "", level: str = "agent",
        *, bridge_call_type: str = "BranchLink", bridge_label: str = "",
    ) -> StateNode:
        """Ensure *elems* (a lane's in-progress sequence) reaches *target_ts*
        as its own bracketing StateNode, inserting a zero-content connector
        TransNode first when the lane doesn't already end there.

        Needed wherever DFS order jumps — forward or backward in real time —
        without a real call of its own to anchor the reset boundary to (see
        the Agents lane's rank>0 fork-branch handling below): the strict
        State/Trans alternation ``_trajectory_validator.py`` enforces means a
        StateNode can never simply be appended next to another StateNode, no
        matter how the timestamps compare. The Calls lane doesn't need this —
        every branch already has a real call record to tag as `_branch_entry`
        (see tree.py) — but the Agents lane has no such record to synthesize
        one from, so a plain connector plays that role instead.

        ``bridge_call_type``/``bridge_label`` let a caller replace the default
        "BranchLink" (invisible layout artefact, used for genuine DFS branch
        jumps — see the fork/reset handling below) with a real, visible
        connector for callers that know *target_ts* isn't actually a
        different branch, just a same-instant boundary recorded a hair later
        by an independent event source (see the wait_state RESUME call site).
        """
        prev = elems[-1]
        node = state(target_ts, hover, level)
        if node is prev:
            return node
        # Guard against a DFS-position regression: target_ts may already be
        # registered from an EARLIER-in-DFS-order fragment that happened to
        # land on this exact real timestamp by coincidence — e.g. a fork's
        # rank-0 branch's own end, when a LATER sibling's merge-computed
        # "delegating agent resumes after every branch joins" boundary
        # (_make_agent_sequence) lands on that same wall-clock instant
        # because that earlier branch happened to be the last one to finish
        # in real time even though DFS visited it first. Reusing that node's
        # dfs_pos here would walk this lane backward. Mint a fresh, distinct
        # real ts a hair after prev's own instead of touching the pre-existing
        # node — other lanes may still reference it correctly — the same
        # technique tree.py's _MARKER_DUR/_reserve_marker_width already use
        # elsewhere to keep genuinely-different moments from colliding on one
        # timestamp.
        #
        # NOTE: this only catches the case where target_ts is ALREADY
        # registered in _ts_to_dfs_pos with a stale value at the time this
        # runs. A fork whose branches complete in a different order than DFS
        # visited them can still leave the delegating agent's own tail
        # fragment (resuming after every branch joins) with an imperfect
        # position when target_ts isn't covered yet either — the generic
        # real-time interpolation fallback (dag.py's later pass) has no DFS
        # awareness. That narrower case is not fully solved here; see
        # test_multilevel_plot_dfs_axis.py's module docstring for the
        # regression coverage this fix does guarantee.
        _existing_pos = _ts_to_dfs_pos.get(target_ts)
        _prev_pos      = _ts_to_dfs_pos.get(prev.ts, 0.0)
        if _existing_pos is not None and _existing_pos < _prev_pos:
            _fresh_ts = prev.ts + 1e-3
            while _fresh_ts in state_reg:
                _fresh_ts += 1e-3
            node = state(_fresh_ts, hover, level)
            _ts_to_dfs_pos[_fresh_ts] = _prev_pos + 1e-3
            target_ts = _fresh_ts
        elems.append(TransNode(
            node_id=f"tr-bridge-{next_seq()}",
            call_type=bridge_call_type,
            label=bridge_label,
            start_ts=prev.ts,
            end_ts=target_ts,
            level=level,
            agent_id="",
            seq=next_seq(),
            is_instant=True,
            hover_in=hover if bridge_call_type != "BranchLink" else "layout connector — DFS branch entry, not a real call",
            hover_out=hover if bridge_call_type != "BranchLink" else "layout connector — DFS branch entry, not a real call",
        ))
        elems.append(node)
        return node

    def _provenance_block(rec: dict, ct: str) -> str:
        """Build a provenance triplet block for hover enrichment.

        Returns a compact multi-line string of ``(subject, predicate, object)``
        triplets that describe the structural graph context of the call.
        Empty string when nothing meaningful can be derived (e.g. Session).
        """
        lines: list[str] = []
        _cid = rec.get("call_id", "")
        _aid = rec.get("agent_id", "")

        # (call, type, CallType)
        lines.append(f"(call, type, {ct})")

        # (call, executedBy, agent)
        if _aid:
            lines.append(f"(call, executedBy, {_aid})")

        # (call, containedIn, parent)
        _pid = parent_of.get(_cid)
        if _pid:
            _prec = rec_by_id.get(_pid)
            if _prec:
                _pt = _prec.get("call_type", "?")
                _pa = _prec.get("agent_id") or _prec.get("label") or _pid[:12]
                lines.append(f"(call, containedIn, {_pa} [{_pt}])")

        # (call, usedModel, model) — LLMCall only
        _model = rec.get("model", "")
        if _model and ct in ("LLMCall", "MITMCall"):
            lines.append(f"(call, usedModel, {_model})")

        # (call, invoked, tool) — ToolCall only
        _tool = rec.get("tool_name", "")
        if _tool:
            lines.append(f"(call, invoked, {_tool})")

        # (call, children, N) — non-leaf calls
        _kids = children_of.get(_cid, [])
        if _kids:
            _child_types = defaultdict(int)
            for k in _kids:
                _child_types[k.get("call_type", "?")] += 1
            _parts = [f"{cnt}×{ctype}" for ctype, cnt in _child_types.items()]
            lines.append(f"(call, contains, {' '.join(_parts)})")

        if len(lines) <= 1:
            return ""
        return "\n\n🔗 Provenance:\n" + "\n".join(f"  {l}" for l in lines)

    def trans(
        rec: dict, *,
        lane_level: str,
        node_id: str,
        call_type: Optional[str] = None,
        label: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> TransNode:
        ct  = call_type or rec.get("call_type", "AgentCall")
        lbl = label or rec.get("label") or TYPE_LABEL.get(ct, ct[:12])
        s   = start_ts if start_ts is not None else rec["start_ts"]
        e   = end_ts   if end_ts   is not None else (rec["end_ts"] or t_max)
        # Enrich hover_in with L2/L3 annotation summary (XAI)
        base_in  = rec.get("input", "")
        ann_lines = ann_map.get(rec.get("call_id", ""), [])
        hint     = ("\n\n[" + " | ".join(ann_lines) + "]") if ann_lines else ""
        # For LLMCall, prepend the call_id so it's visible in the hover panel.
        _cid = rec.get("call_id", "")
        _model = rec.get("model", "")
        call_id_prefix = ""
        if ct == "LLMCall" and _cid:
            _meta_parts = [f"call_id: {_cid}"]
            if _model:
                _meta_parts.append(f"model: {_model}")
            call_id_prefix = "📋 " + "  ·  ".join(_meta_parts) + "\n\n"
        _raw_out = rec.get("output", "")
        if ct == "LLMCall" and not _raw_out:
            _syn_decision = llm_tool_decisions.get(_cid)
            if _syn_decision:
                _raw_out = _syn_decision
        _thinking_txt = rec.get("thinking", "") if ct == "LLMCall" else ""
        _missing_telemetry: list[str] = []
        if rec.get("_end_missing"):
            _missing_telemetry.append("end_event")
        if _thinking_txt:
            _hover_out = f"🧠 Thinking:\n\n{_thinking_txt}\n\n---\n\n{_raw_out}".strip()
        else:
            _hover_out = _raw_out
        # ProcessingCall: enrich hoverIn with the operation name header and
        # mark hoverOut as sentinel so JS can hide the useless "assembled" line.
        if ct == "ProcessingCall":
            _proc_name = rec.get("processing_name", "")
            # Detect fallback case: input == raw processing_type name (not real task)
            _is_type_name_fallback = base_in in _PROC_TYPE_LABEL
            if _is_type_name_fallback:
                _missing_telemetry.append("input")
            _task_in = "" if _is_type_name_fallback else base_in
            _op_header = f"[{_proc_name}]\n" if _proc_name else ""
            base_in    = (_op_header + _task_in).strip()
            # Clear output if it's just a status marker or a processing type name
            if _hover_out in ("", "assembled") or _hover_out in _PROC_TYPE_LABEL:
                _hover_out = ""
            if ct == "ProcessingCall" and rec.get("processing_name") == "context assembly":
                # The CPR card already renders the assembled prompt parts for this
                # synthetic ProcessingCall. Repeating the raw prompt text in the
                # output section makes the same system/context content appear twice.
                _hover_out = ""
        if _missing_telemetry:
            _missing_note = f"[missing telemetry: {', '.join(_missing_telemetry)}]"
            base_in = (_missing_note + ("\n" + base_in if base_in else "")).strip()
        # Build structured context provenance data for rich JS rendering.
        # For ProcessingCall records that carry their own ``segments`` (per-actor
        # spans emitted by ObservabilityPlugin), build provenance from those segments
        # directly instead of from the provenance_map (which maps LLMCall call_ids).
        # Semantic fields (actor, trigger, via) are derived from (role, source) using
        # _derive_context_semantics() — the normalized data model principle.
        _rec_segments = rec.get("segments") if (show_provenance and ct == "ProcessingCall") else None
        if _rec_segments:
            _cpr_structured: list[dict] = []
            for _s in _rec_segments:
                _prov = _s.get("provenance") or {}
                _psrc = _s.get("source", "?")
                _pmech = _prov.get("mechanism", "inject")
                _role = _s.get("role") or _prov.get("role") or ""
                _role_defs = _derive_context_semantics(_role, _psrc)
                _entry: dict = {
                    "source": _psrc,
                    "category": _source_category(_psrc, _pmech),
                    "mechanism": _pmech,
                    "retrieval": _prov.get("retrieval", ""),
                    "decision": _prov.get("decision", ""),
                    "causeType": _prov.get("cause_type") or _role_defs["causeType"],
                    "cause": _prov.get("cause") or _role_defs["cause"],
                    "tokens": _s.get("tokens") or 0,
                    "retained": True,
                    "placement": _s.get("placement", ""),
                    "content": _s.get("content", ""),
                    "sectionId": _s.get("section_id", ""),
                    "sourceType": _prov.get("source_type") or _s.get("role") or "",
                    "sourceId": _prov.get("source_id", ""),
                    "trigger": _prov.get("trigger") or _role_defs["trigger"],
                    "actor": _prov.get("actor") or _role_defs["actor"],
                    "via": _prov.get("via") or _role_defs["via"],
                }
                _ann = _prov.get("annotations")
                if _ann:
                    _entry["annotations"] = _ann
                _meta = _s.get("metadata")
                if _meta:
                    _entry["metadata"] = _meta
                _enrich = _prov.get("enrichments")
                if _enrich:
                    _entry["enrichments"] = _enrich
                _chain = _prov.get("chain")
                if _chain:
                    _entry["chain"] = _chain
                _cpr_structured.append(_entry)
            _cpr_raw = []  # provenance collected at call level only
        else:
            _cpr_raw = cpr_map.get(_cid, [])
            _cpr_structured = []
            for _p in _cpr_raw:
                _psrc = _p.get("source", "?")
                _pmech = _p.get("mechanism", "inject")
                _role = _p.get("role") or ""
                # Derive semantic fields from role + source (normalized data model)
                _role_defs = _derive_context_semantics(_role, _psrc)
                _entry: dict = {
                    "source": _psrc,
                    "category": _source_category(_psrc, _pmech),
                    "mechanism": _pmech,
                    "retrieval": _p.get("retrieval", ""),
                    "decision": _p.get("decision", ""),
                    "causeType": _p.get("cause_type") or _role_defs["causeType"],
                    "cause": _p.get("cause") or _role_defs["cause"],
                    "tokens": _p.get("token_estimate", 0),
                    "retained": _p.get("retained", True),
                    "placement": _p.get("placement", ""),
                    "content": _p.get("content") or _p.get("content_preview") or "",
                    "sectionId": _p.get("section_id", ""),
                    "sourceType": _p.get("source_type") or _p.get("role") or "",
                    "sourceId": _p.get("source_id", ""),
                    "trigger": _p.get("trigger") or _role_defs["trigger"],
                    "actor": _p.get("actor") or _role_defs["actor"],
                    "via": _p.get("via") or _role_defs["via"],
                }
                _ann = _p.get("annotations")
                if _ann:
                    _entry["annotations"] = _ann
                _meta = _p.get("metadata")
                if _meta:
                    _entry["metadata"] = _meta
                _enrich = _p.get("enrichments")
                if _enrich:
                    _entry["enrichments"] = _enrich
                _chain = _p.get("chain")
                if _chain:
                    _entry["chain"] = _chain
                _cpr_structured.append(_entry)
        _gov_all = gov_map.get(_cid, [])
        _gov_notable = notable_governance(_gov_all)
        _gov_egress = [g for g in _gov_notable if g.get("hook") == "egress"]
        _gov_ingress = [g for g in _gov_notable if g.get("hook") == "ingress"]
        _cpr_mode = str(rec.get("cpr_mode") or "")
        _context_operation = str(rec.get("context_operation") or "")
        return TransNode(
            node_id=node_id,
            call_type=ct,
            label=lbl,
            start_ts=s,
            end_ts=e,
            level=lane_level,
            agent_id=rec.get("agent_id", ""),
            seq=next_seq(),
            call_id=_cid,
            hover_in=(call_id_prefix + base_in + hint).strip(),
            hover_out=_hover_out,
            missing_telemetry=_missing_telemetry,
            is_instant=(ct not in ("AgentCall", "Session", "MASCall", "ProcessingCall") and abs(e - s) <= _TS_TOL),
            cpr_data=_cpr_structured,
            model=_model,
            processing_type=str(rec.get("processing_type") or ""),
            processing_name=str(rec.get("processing_name") or ""),
            # notable_governance: suppress the badge overlay for plain ALLOW/LOG
            # (nearly every call has one) — only surface it when a decision
            # actually changed the outcome. The dedicated Governance lane below
            # shows every decision unfiltered; this only gates the overlay badge.
            governance=_gov_notable,
            governance_egress=_gov_egress,
            governance_ingress=_gov_ingress,
            retry_group_id=retry_map.get(_cid, {}).get("groupId", ""),
            retry_attempt=retry_map.get(_cid, {}).get("attempt", 0),
            cpr_mode=_cpr_mode,
            context_operation=_context_operation,
            # A "wait" ProcessingCall is the agent idling for another actor
            # (e.g. a delegate) — structurally real (it must not collapse the
            # boundary), but rendering it as a colored bar+label+badge like
            # any other processing step is pure clutter: there's no actual
            # work to show. connector_only hides the bar while the lifeline
            # (always drawn between states) still bridges it, so there is
            # neither a visible node NOR a gap. The "parallel fork" marker
            # (processing_type == "parallel_group") gets the same treatment —
            # a fork is a control boundary, not a processing step, already
            # represented by the is_fork StateNode it brackets (see the
            # fork/join handling below), not by its own bar. Its "parallel
            # aggregation" counterpart is deliberately DIFFERENT: combining N
            # branches' outputs into one is genuine work (the user-visible
            # "processing node that aggregates results"), so it stays a real,
            # visible bar/badge instead of being folded away like the fork.
            connector_only=(
                ct == "ProcessingCall"
                and (
                    str(rec.get("processing_type") or "") == PROCESSING_TYPE_WAIT_STATE
                    or (
                        str(rec.get("processing_type") or "") == PROCESSING_TYPE_PARALLEL_GROUP
                        and str(rec.get("processing_name") or "").strip().lower() == PROCESSING_NAME_PARALLEL_FORK
                    )
                )
            ),
        )

    lanes: list[LaneDef] = []

    # ── Session lane: one wide transition covering the full session ──────────
    s0 = state(t_min, s_in,  "session")
    s1 = state(t_max, s_out, "session")
    sess_lane = LaneDef("session", "session", "Session")
    sess_lane.sequence = [
        s0,
        TransNode(
            node_id="tr-session",
            call_type="Session",
            label="Session",
            start_ts=s0.ts,
            end_ts=s1.ts,
            level="session",
            agent_id="",
            seq=next_seq(),
            hover_in=s_in,
            hover_out=s_out,
        ),
        s1,
    ]
    lanes.append(sess_lane)

    def _hitl_between(t0: float, t1: float) -> Optional[dict]:
        """Find the human's actual question and answer for a gap between two
        MAS invocations, so the HITL bar shows what was asked and decided
        instead of an empty box."""
        for ev in events:
            if ev.get("kind") != "hitl_request":
                continue
            ts = float(ev.get("timestamp") or 0)
            if t0 - _TS_TOL <= ts <= t1 + _TS_TOL:
                return hitl_map.get(ev.get("correlation_id"))
        return None

    # ── MAS lane: one transition per MAS invocation, HITL gaps between them ──
    mas_lane = LaneDef("mas", "mas", "MAS")
    if mas_records:
        elems: list = [state(t_min, s_in, "session")]
        _prev_mas_end: Optional[float] = None
        for i, mrec in enumerate(mas_records):
            end_ts = mrec["end_ts"] if mrec.get("end_ts", 0) > 0 else t_max
            start_ts, end_ts = _mas_lane_span(mrec["start_ts"], end_ts, _prev_mas_end)
            elems.append(trans(mrec, lane_level="mas", node_id=f"tr-mas-{i}",
                               call_type="MASCall", label="MAS",
                               start_ts=start_ts, end_ts=end_ts))
            elems.append(state(end_ts, mrec.get("output", ""), "mas"))
            _prev_mas_end = end_ts
            if i + 1 < len(mas_records) and mas_records[i + 1]["start_ts"] < end_ts:
                # Overlapping invocations: there is no human gap to show.
                continue
            if i + 1 < len(mas_records):
                nxt = mas_records[i + 1]
                hitl_rec: dict = {"input": "", "output": "", "agent_id": ""}
                _hx = _hitl_between(end_ts, nxt["start_ts"])
                if _hx:
                    hitl_rec["input"] = _hx.get("question", "")
                    _resolution = _hx.get("resolution", "")
                    _answer = _hx.get("answer", "")
                    if _resolution or _answer:
                        hitl_rec["output"] = f"[{_resolution}] {_answer}".strip()
                elems.append(trans(hitl_rec, lane_level="mas", node_id=f"tr-hitl-{i}",
                                   call_type="HITL", label="HITL",
                                   start_ts=end_ts, end_ts=nxt["start_ts"]))
                elems.append(state(nxt["start_ts"], "", "mas"))
        if not (isinstance(elems[-1], StateNode) and elems[-1] is state_reg.get(t_max)):
            # A bare append here would leave two consecutive StateNodes with
            # no Trans between them (t_max can be inflated well past this
            # lane's last real state by an unrelated lane's synthetic
            # end-missing placeholder, or by tail ProcessingCall staggering
            # nudging the Calls lane's true final ts a hair later — see
            # records.py's _end_missing fallback and
            # _stagger_coinc_processing_calls) — bridge it instead, same as
            # the Agent lane below. An exact object-identity check (not a
            # time-tolerance one) is required here: a gap that's small
            # enough to fall within the visual _TS_TOL is still a REAL,
            # distinct StateNode — skipping the bridge would leave this
            # lane's exit disconnected from the shared session boundary
            # object the renderer needs for the final connector.
            # _bridge_to_state itself already no-ops when elems[-1] is
            # already the target node, so calling it unconditionally here is
            # safe.
            _bridge_to_state(elems, t_max, s_out, "session")
        mas_lane.sequence = elems
    else:
        empty: dict = {"input": s_in, "output": s_out, "agent_id": ""}
        mas_lane.sequence = [
            state(t_min),
            trans(empty, lane_level="mas", node_id="tr-mas-0",
                  call_type="MASCall", label="MAS",
                  start_ts=t_min, end_ts=t_max),
            state(t_max),
        ]
    lanes.append(mas_lane)

    # ── Agent lane: DFS-derived sequence, delegation splits included ─────────

    # Fork/Branch groups (see fork_groups above) render sequentially along the
    # DFS virtual-position axis with a reset separator between them — there
    # is no side-by-side slot layout for overlapping siblings anymore; every
    # fork's branches, overlapping or not, are drawn one after another.
    #
    # The VISUAL fork/join badge (is_fork/is_join, parallel_group_id,
    # parallel_size) is a narrower notion than fork_groups' own tree-
    # structural grouping, though: fork_groups groups ANY 2+ delegate calls
    # sharing a delegating parent, including plain sequential/solo
    # delegations that just happen to share it with an unrelated, later,
    # genuine fork (e.g. a moderator that delegates once, does other work,
    # then delegates twice concurrently later) — grouping them for the DFS
    # reset/call-sequence separator machinery above is correct and desired,
    # but visually badging the solo delegation as part of an N-way parallel
    # fork is not (regression: a solo delegation was shown as
    # ``parallelSize=3`` purely because of this tree-structural coincidence).
    # Only the sub-cluster(s) of members the runtime itself genuinely
    # dispatched together in ONE native ``parallel_group`` event (see
    # _native_parallel_group_by_tool_call_id) get a fork/join badge here.
    _native_group_by_tool_call_id = _native_parallel_group_by_tool_call_id(records)
    _parallel_info: dict[str, tuple[str, int, int]] = {}  # agent call_id → (group_id, rank, size)
    _parallel_members: dict[str, list[str]] = {}  # group_id -> agent call_ids in fork order
    for _members in fork_groups.values():
        _native_sub_groups: dict[str, list[dict]] = defaultdict(list)
        for _r in _members:
            _tool_rec = rec_by_id.get(parent_of.get(_r["call_id"]) or "")
            _tool_call_id = str((_tool_rec or {}).get("call_id") or "")
            _native_gid = _native_group_by_tool_call_id.get(_tool_call_id)
            if _native_gid:
                _native_sub_groups[_native_gid].append(_r)
        for _native_gid, _sub_members in _native_sub_groups.items():
            if len(_sub_members) < 2:
                continue
            _sub_members = sorted(_sub_members, key=lambda r: r["start_ts"])
            _gid = f"fork-{_native_gid}"
            _parallel_members[_gid] = [str(_r["call_id"]) for _r in _sub_members]
            for _rank, _r in enumerate(_sub_members):
                _parallel_info[_r["call_id"]] = (_gid, _rank, len(_sub_members))

    # Fork-point ts per group (captured the moment the fork state is built,
    # rank 0's own entry) — needed later to (a) stamp the join state's own
    # "back to fork" link and (b) build the lane-level parallelSpans band
    # (fork ts → join ts) once the matching join is reached. Both renderer
    # features rely purely on native fork/join facts already computed here,
    # no heuristics.
    _fork_ts_by_group: dict[str, float] = {}
    _parallel_group_spans: list[dict] = []  # [{startTs, endTs, size}, ...] for lane JSON
    # Every branch's OWN actual entry ts (its real st_start.ts, in rank
    # order) — recorded live as each rank is visited, NOT re-derived from
    # _reset_ts_by_branch_id (that map only covers ranks whose entry needed a
    # DFS-position reset; a rank that happens to follow naturally in both
    # real time AND DFS order — e.g. immediately after the previous branch's
    # own end — is never in it, so guessing from it would silently collapse
    # that branch's link onto the wrong ts). Backfilled onto the fork state
    # itself once every rank has been visited (at join time).
    _branch_entries_by_group: dict[str, list[float | None]] = {}
    # Every branch's OWN actual exit ts (its real st_end.ts) — updated live
    # every time a fragment belonging to that rank is processed, so the
    # LAST write (in DFS-traversal order) is that branch's true final end,
    # even for a branch that itself further delegates and so has multiple
    # agent_sequence fragments (pre/tail). Used only to stamp is_branch_end
    # markers (see StateNode.is_branch_end) at join time — a purely visual
    # "this individual branch ends here" fact, distinct from is_join (which
    # marks only the last-ranked branch's own end, reused as the fork/join
    # boundary state).
    _branch_exits_by_group: dict[str, list[float | None]] = {}

    if agent_sequence:
        agent_lane = LaneDef("agents", "agent", "Agents")
        elems = [state(t_min, s_in, "session")]
        _agent_branch_started: set[str] = set()  # rank>0 call_ids already bridged
        _agent_branch_begin_marked: set[tuple[str, int]] = set()  # (gid, rank) already is_branch_begin-tagged
        for i, arec in enumerate(agent_sequence):
            short  = arec["agent_id"].split(".")[-1][:18]
            end_ts = arec["end_ts"] if arec.get("end_ts", 0) > 0 else t_max
            _par   = _parallel_info.get(arec["call_id"])  # (group_id, rank, size) or None

            # Populate the START state of each agent fragment with its input so
            # delegation handoff boundaries show the task being passed in.
            #
            # In the common case elems[-1] IS already this exact ts — the
            # previous fragment's st_end placed the same StateNode object
            # there — so _bridge_to_state's identity check makes this a no-op
            # (no new element appended). But that assumption can break in more
            # ways than just "rank > 0's first fragment" (a fork's 2nd..Nth
            # sibling, whose own subtree hasn't been explored yet — DFS went
            # back up the call tree and down into a new sibling, often a real
            # backward jump relative to elapsed time): a fork whose branches
            # finish in a DIFFERENT order than DFS visited them also leaves
            # the delegating agent's own tail fragment (resuming after ALL
            # branches join) starting at a timestamp elems[-1] never reached —
            # elems[-1] is whichever branch DFS happened to visit last, not
            # necessarily the one that ended last in real time. Bridging
            # unconditionally (instead of only for the rank > 0 case) makes
            # every fragment boundary robust to both, with no cost for the
            # (overwhelming majority of) fragments where the assumption holds.
            _target_ts = arec["start_ts"]
            if _par and _par[1] > 0 and arec["call_id"] not in _agent_branch_started:
                # rank > 0's first fragment — only the branch's FIRST
                # agent_sequence fragment needs this; a delegate that itself
                # further delegates gets split into pre/tail fragments, and
                # only the pre fragment is the true branch entry (the tail
                # fragment falls through to the plain arec["start_ts"] above
                # like any other continuation).
                #
                # Bridge to the branch's own _branch_entry timestamp
                # (_reset_ts_by_branch_id), NOT arec["start_ts"] — the two are
                # usually only ~1ms apart in principle (the delegate's own
                # execution starts just after its dispatching tool call, per
                # _align_record_boundaries), but both are computed from the
                # SAME "dispatch instant + _MARKER_DUR" arithmetic as a
                # sibling batch's *other* delegation markers, so
                # arec["start_ts"] can land exactly on some unrelated
                # marker's own timestamp — reusing that ts here would
                # silently steal the marker's already-correct (intentionally
                # clustered, not reset) dfs_pos instead of getting a reset
                # position of its own. The branch's own _branch_entry
                # timestamp has no such collision — it is unique to this
                # branch by construction — and already has a correct,
                # monotonic dfs_pos from _assign_dfs_positions.
                _target_ts = _reset_ts_by_branch_id.get(arec["call_id"], arec["start_ts"])
                _agent_branch_started.add(arec["call_id"])

            st_start = _bridge_to_state(elems, _target_ts, arec.get("input", ""))
            if _par:
                # Record this rank's OWN real entry ts for the fork's
                # eventual branch-navigation list (see _branch_entries_by_group
                # above) — for EVERY rank, not just rank 0, since only the
                # live st_start.ts is trustworthy.
                _gid, _rank, _size = _par
                _branch_entries_by_group.setdefault(_gid, [None] * _size)[_rank] = st_start.ts
                # Branch-begin marker: this individual branch's own entry
                # point (distinct from is_fork, which marks only the shared
                # split boundary — rank 0's begin coincides with it, both
                # flags can be true on the same StateNode). Tagged only the
                # FIRST time this rank is seen — a branch that itself further
                # delegates gets split into pre/tail agent_sequence fragments
                # sharing the same call_id, and only the pre fragment is the
                # branch's true entry (see _target_ts override above).
                if (_gid, _rank) not in _agent_branch_begin_marked:
                    st_start.is_branch_begin = True
                    _agent_branch_begin_marked.add((_gid, _rank))
                if _rank == 0:
                    # Fork state: mark the boundary where parallel branches
                    # split. For rank 0 the fork state is (almost always)
                    # already the previous iteration's st_end.
                    st_start.is_fork           = True
                    st_start.parallel_group_id = _gid
                    st_start.parallel_size     = _size
                    # This loop only ever runs over agent_sequence fragments
                    # (see _parallel_info's construction above, narrowed to
                    # native sub-clusters of genuine cross-AGENT delegation
                    # siblings) — every fork built here is by definition an
                    # agent-level fork, never a single agent's own tool fan-out.
                    st_start.fork_kind = "agent"
                    _fork_ts_by_group[_gid] = st_start.ts
            st_start.hover_by_lane["agents"] = arec.get("input", "")

            # st_start.ts is the authoritative start for the real Trans below
            # too — _bridge_to_state may have minted a fresh ts distinct from
            # arec["start_ts"] (the branch-entry override above, or the
            # dfs_pos-regression guard inside _bridge_to_state itself), and
            # the Trans must agree with its own preceding StateNode so
            # dfs_pos_start resolves to the same, already-correct position
            # rather than falling through to a stale or interpolated one.
            _tr = trans(arec, lane_level="agent", node_id=f"tr-agent-{i}",
                        label=short, start_ts=st_start.ts, end_ts=end_ts)
            if _par:
                _gid, _rank, _size = _par
                _tr.parallel_group_id = _gid
                _tr.parallel_rank     = _rank
                _tr.parallel_size     = _size
            elems.append(_tr)
            _agent_output = arec.get("output", "")
            # Suppress bookkeeping strings that carry no semantic content.
            if _agent_output.startswith("Completed tool calls"):
                _agent_output = ""
            # Use "agent_end" level (priority 4 > call=3) so the agent's
            # returned output is the authoritative content for the state box
            # at this boundary — not a call-layer own_out/next_inp value.
            _end_level = "agent_end" if _agent_output else "agent"
            st_end = state(end_ts, _agent_output, _end_level)
            st_end.hover_by_lane["agents"] = _agent_output
            # Mark the state as interrupted only when the agent genuinely ran to
            # the trace cut-off (end_ts ≈ t_max).  Pre-delegation fragments of
            # the same parent record also carry _end_missing=True but have their
            # own explicit end_ts (child start), so they must NOT be flagged.
            if arec.get("_end_missing") and abs(end_ts - t_max) <= _TS_TOL:
                st_end.is_interrupted = True
            elif arec.get("_exec_status"):
                st_end.is_error = True
            if _par:
                _gid, _rank, _size = _par
                # Branch-exit tracking: overwritten on every fragment sharing
                # this rank's call_id, so the LAST write (DFS-traversal
                # order) is this branch's true final end_ts — used below
                # (finalized once every rank has been visited, at join time)
                # to stamp is_branch_end, symmetric with is_branch_begin.
                _branch_exits_by_group.setdefault(_gid, [None] * _size)[_rank] = st_end.ts
                if _rank == _size - 1:
                    # Join state: last branch ends at the fork/join boundary
                    st_end.is_join           = True
                    st_end.parallel_group_id = _gid
                    st_end.parallel_size     = _size
                    st_end.fork_kind         = "agent"  # see fork_kind comment above
                    st_end.join_of           = list(_parallel_members.get(_gid, []))
                    st_end.join_fork_ts      = _fork_ts_by_group.get(_gid)
                    if _gid in _fork_ts_by_group:
                        _parallel_group_spans.append({
                            "startTs": _fork_ts_by_group[_gid],
                            "endTs":   st_end.ts,
                            "size":    _size,
                        })
                        # Every rank has now been visited (DFS/fork order) —
                        # backfill the fork state's own branch-navigation
                        # list with each branch's real recorded entry ts.
                        _fork_state = state_reg.get(_fork_ts_by_group[_gid])
                        if _fork_state is not None:
                            _fork_state.fork_branch_ts = [
                                _t for _t in _branch_entries_by_group.get(_gid, []) if _t is not None
                            ]
                            # Forward cross-link (mirrors join_fork_ts above,
                            # set on the join state): the fork state now
                            # knows its own matching join's ts too, so
                            # fork<->join are cross-linked bidirectionally.
                            _fork_state.join_ts = st_end.ts
                        # Every rank's own final exit ts is now known — stamp
                        # is_branch_end on each of those states (rank _size-1's
                        # exit IS this same st_end, already is_join=True; both
                        # flags coexist on that one StateNode), and cross-link
                        # each rank's own begin<->end pair bidirectionally
                        # (branch_partner_ts) — by rank, never by ts-proximity,
                        # since concurrent branches can finish out of order.
                        _entries_for_gid = _branch_entries_by_group.get(_gid, [])
                        for _rank, _exit_ts in enumerate(_branch_exits_by_group.get(_gid, [])):
                            if _exit_ts is None:
                                continue
                            _exit_state = state_reg.get(_exit_ts)
                            if _exit_state is not None:
                                _exit_state.is_branch_end = True
                                _entry_ts = _entries_for_gid[_rank] if _rank < len(_entries_for_gid) else None
                                if _entry_ts is not None:
                                    _exit_state.branch_partner_ts = _entry_ts
                                    _entry_state = state_reg.get(_entry_ts)
                                    if _entry_state is not None:
                                        _entry_state.branch_partner_ts = _exit_ts
            elems.append(st_end)
        if not (isinstance(elems[-1], StateNode) and elems[-1] is state_reg.get(t_max)):
            # The DFS-last agent fragment isn't necessarily the real-time-last
            # one — e.g. a fork's earlier-ranked sibling can run long past the
            # point DFS order puts it, so this lane's own final element can
            # land well before t_max. A bare append here would leave two
            # consecutive StateNodes with no Trans between them (the same
            # class of bug rank>0 branches hit above); bridge it instead. An
            # exact object-identity check (not a time-tolerance one) is
            # required — see the MAS lane's matching guard above for why a
            # small but real gap must still be bridged.
            _bridge_to_state(elems, t_max, s_out, "session")
        agent_lane.sequence = elems
        agent_lane.parallel_spans = _parallel_group_spans
        lanes.append(agent_lane)

    # ── Call lane: direct call children of each agent fragment ───────────────
    if call_sequence:
        call_lane = LaneDef("calls", "call", "Calls")
        elems = [state(t_min, s_in, "session")]
        _wait_records: dict[str, dict[str, dict]] = defaultdict(dict)
        for _r in call_sequence:
            if str(_r.get("call_type") or "") != "ProcessingCall":
                continue
            if str(_r.get("processing_type") or "") != PROCESSING_TYPE_WAIT_STATE:
                continue
            _wid = str(_r.get("wait_link_id") or "").strip()
            _role = str(_r.get("wait_role") or "").strip().upper()
            if _wid and _role in {"WAIT", "RESUME"}:
                _wait_records[_wid][_role] = _r

        def _dispatch_tool_call_for_wait(_wait_rec: dict) -> dict | None:
            # Find the delegation ToolCall a WAIT/RESUME record is blocking
            # on. When 2+ delegations are dispatched concurrently by the
            # SAME parent call (a genuine fork), agent_id+parent_call_id
            # alone is AMBIGUOUS — every branch's own wait_rec shares both
            # fields. Prefer an EXACT match first: the runtime's own
            # wait_link_id is minted as f"delegate-{correlation_id}" (see
            # driver.py's _record_wait_state_obs), and the dispatching
            # ToolCall record itself carries that same correlation_id — so
            # parse it out of wait_link_id and match directly, which is
            # unambiguous even for N concurrent branches. Only fall back to
            # the looser agent_id+parent_call_id+timing heuristic (latest
            # ToolCall that already ended by the WAIT's own ts) when no
            # parseable/matching correlation_id is available (older traces).
            _agent = str(_wait_rec.get("agent_id") or "")
            _parent = str(_wait_rec.get("parent_call_id") or "")
            _t0 = float(_wait_rec.get("start_ts") or 0.0)
            _wlid = str(_wait_rec.get("wait_link_id") or "").strip()
            _corr = _wlid.rsplit("-", 1)[-1] if _wlid.startswith("delegate-") else ""
            if _corr:
                for _cand in call_sequence:
                    if str(_cand.get("call_type") or "") != "ToolCall":
                        continue
                    if str(_cand.get("agent_id") or "") != _agent:
                        continue
                    if str(_cand.get("correlation_id") or "") == _corr:
                        return _cand
            _best: dict | None = None
            for _cand in call_sequence:
                if str(_cand.get("call_type") or "") != "ToolCall":
                    continue
                if str(_cand.get("agent_id") or "") != _agent:
                    continue
                if _parent and str(_cand.get("parent_call_id") or "") != _parent:
                    continue
                _end = float(_cand.get("end_ts") or 0.0)
                if _end > _t0 + _TS_TOL:
                    continue
                if _best is None or _end > float(_best.get("end_ts") or 0.0):
                    _best = _cand
            return _best

        def _native_group_for_wait(_wait_rec: dict) -> str:
            # Does this WAIT's own delegation dispatch belong to a genuine
            # native ``parallel_group`` (the runtime's own real fork/join
            # fact, see ``_native_parallel_group_by_tool_call_id``)?
            _best = _dispatch_tool_call_for_wait(_wait_rec)
            if _best is None:
                return ""
            return _native_group_by_tool_call_id.get(str(_best.get("call_id") or ""), "")

        # A "wait-fork" cluster: 2+ concurrent delegation waits dispatched by
        # the SAME call (same parent_call_id) whose own [wait_ts, resume_ts]
        # busy windows overlap — the delegating agent fired off N branches at
        # once and is genuinely waiting for all of them, not N independent
        # sequential waits that happen to be adjacent. This STAGGER-only
        # collapsing (no genuine native ``parallel_group`` behind it — e.g.
        # N delegates dispatched within microseconds of each other by
        # separate, sequential decisions) still collapses the whole cluster
        # onto its FIRST (earliest-dispatched) member, exactly as before.
        # But when the WAIT genuinely belongs to a native ``parallel_group``
        # (a real, single dispatch decision — see ``_native_group_for_wait``
        # above), each member must stay its OWN independent singleton
        # "cluster" instead: the Agents lane already carries the one shared
        # fork/join badge for the whole group (``_parallel_info``, built
        # above from this exact same native fact), so this Calls lane's own
        # job here is just to give each branch's own WAIT/RESUME pair its
        # own distinct, VISIBLE label/state/content — never collapsed or
        # hidden behind a representative, and never re-tagged with its own
        # redundant is_fork/is_join badge either (see the "if _wf_is_cluster"
        # tagging below, which now naturally never fires for these).
        _wait_cluster_rep: dict[str, str] = {}            # every member wid -> representative wid
        _wait_cluster_members: dict[str, list[str]] = {}  # representative wid -> member wids (dispatch order)
        _wait_intervals: dict[str, tuple[float, float, str]] = {}
        _native_wait_gid: dict[str, str] = {}  # wid -> native parallel_group id, only when genuinely native
        for _wid, _pair in _wait_records.items():
            _w, _r = _pair.get("WAIT"), _pair.get("RESUME")
            if not _w or not _r:
                continue
            if str(_w.get("wait_scope") or "").strip().lower() != WAIT_SCOPE_DELEGATION:
                continue
            _wait_intervals[_wid] = (
                float(_w.get("start_ts") or 0.0),
                float(_r.get("start_ts") or 0.0),
                str(_w.get("parent_call_id") or ""),
            )
            _gid = _native_group_for_wait(_w)
            if _gid:
                _native_wait_gid[_wid] = _gid
        for _wid in sorted(_wait_intervals, key=lambda w: _wait_intervals[w][0]):
            _wt, _rt, _parent = _wait_intervals[_wid]
            _rep = None
            if _wid not in _native_wait_gid:
                _rep = next(
                    (
                        _cand for _cand in _wait_cluster_members
                        if _cand not in _native_wait_gid
                        and _wait_intervals[_cand][2] == _parent and _wt < _wait_intervals[_cand][1]
                    ),
                    None,
                )
            if _rep is None:
                _wait_cluster_members[_wid] = [_wid]
                _wait_cluster_rep[_wid] = _wid
            else:
                _wait_cluster_members[_rep].append(_wid)
                _wait_cluster_rep[_wid] = _rep
        _wait_fork_branch_ts: dict[str, list[float]] = {
            _rep: sorted(_wait_intervals[_m][0] for _m in _members)
            for _rep, _members in _wait_cluster_members.items()
            if len(_members) >= 2
        }

        _wait_ord: dict[str, int] = {}

        def _wait_idx(_wid: str) -> int:
            _key = _wait_cluster_rep.get(_wid, _wid)
            if _key not in _wait_ord:
                _wait_ord[_key] = len(_wait_ord) + 1
            return _wait_ord[_key]

        def _label_for_wait(_role: str, _wid: str) -> str:
            _idx = _wait_idx(_wid)
            return f"W{_idx}" if _role == "WAIT" else f"R{_idx}"

        def _blocking_tool_for_wait(_wait_rec: dict) -> dict | None:
            return _dispatch_tool_call_for_wait(_wait_rec)

        # The Agents lane (built above) already has its own boundary state
        # for "delegating agent resumes after every branch joins" — recorded
        # a hair apart from this lane's own RESUME wait_state ts because the
        # two come from independent event sources (native observability's
        # wait_state bookkeeping vs. the delegate's own LLMCall/execution_end
        # completion). Snapping to that already-registered ts here (instead
        # of minting a distinct one via the raw crec["start_ts"]) makes both
        # lanes resolve to the SAME shared state_reg entry (see `state()`),
        # so R/W markers land at the identical chart column in both lanes
        # instead of rendering a hair apart.
        _agent_lane_states = agent_lane.sequence if agent_sequence else []

        def _snap_to_agent_lane_ts(candidate_ts: float) -> float:
            _best_ts, _best_d = candidate_ts, _TS_TOL
            for _el in _agent_lane_states:
                if not isinstance(_el, StateNode):
                    continue
                _d = abs(_el.ts - candidate_ts)
                if _d < _best_d:
                    _best_ts, _best_d = _el.ts, _d
            return _best_ts

        def _attach_wait_meta(
            _state: StateNode, *, _wid: str, _role: str, _note: str,
        ) -> None:
            _pair = _wait_records.get(_wid, {})
            _wait_rec = _pair.get("WAIT")
            _resume_rec = _pair.get("RESUME")
            _blocking = _blocking_tool_for_wait(_wait_rec) if _wait_rec else None
            _blocking_tool_name = str((_blocking or {}).get("tool_name") or "")
            _blocking_tool_call_id = str((_blocking or {}).get("call_id") or "")
            _blocking_context = str((_blocking or {}).get("input") or "").strip()
            # The WAIT side's own state keeps its raw record ts (the main
            # loop below never snaps it — see the plain `state()` branch),
            # but the RESUME side's own state IS snapped to the Agents
            # lane's already-registered boundary (`_snap_to_agent_lane_ts`,
            # main loop's RESUME branch) so both lanes share one StateNode.
            # peerTs/resumeTs must mirror that same snap here, or the
            # "Resumes to Rn" link points at a ts a hair away from where R's
            # real StateNode actually lives — `_STATE_POPUP_PAYLOAD` (keyed
            # by exact ts) then finds no match and the click silently does
            # nothing.
            _wait_ts = float((_wait_rec or {}).get("start_ts") or 0.0)
            _resume_ts = _snap_to_agent_lane_ts(float((_resume_rec or {}).get("start_ts") or 0.0)) if _resume_rec else 0.0
            _wait_scope = str((_wait_rec or {}).get("wait_scope") or "").strip().lower()
            _state.wait_meta = {
                "waitLinkId": _wid,
                "role": _role,
                "label": _label_for_wait(_role, _wid),
                "note": _note,
                "isBoundaryEvent": True,
                "isContextCrossing": True,
                "blocking": {
                    "kind": "TOOL_CALL",
                    "toolType": "DELEGATION" if _wait_scope == WAIT_SCOPE_DELEGATION else "TOOL_CALL",
                    "toolName": _blocking_tool_name,
                    "toolCallId": _blocking_tool_call_id,
                    "context": _blocking_context,
                },
                "waitTs": _wait_ts,
                "resumeTs": _resume_ts,
                "peerRole": "RESUME" if _role == "WAIT" else "WAIT",
                "peerLabel": _label_for_wait("RESUME", _wid) if _role == "WAIT" else _label_for_wait("WAIT", _wid),
                "peerTs": _resume_ts if _role == "WAIT" else _wait_ts,
            }

        # Native parallel_group members beyond the first (rank>0): their own
        # start_ts is a genuinely earlier-than-elems[-1].ts real timestamp
        # (all branches dispatch at ~the same real instant, but DFS visits
        # them one at a time, fully draining rank0's own subtree first) — the
        # ordinary `state()` call below would still mint a StateNode for that
        # ts, but the docstring's own historical claim ("the Calls lane
        # doesn't need bridging, every branch has a real call record")
        # assumed that record's own start-state would naturally chain from
        # whatever elems already ends on. That's false for a genuine
        # multi-branch native fork: rank0's own real duration means elems
        # ends on RANK0'S OWN content (e.g. its real output) by the time
        # rank1's own record is reached, so rank1's un-bridged "start state"
        # is silently never appended to elems at all, and rank1's own trans
        # ends up rendered right after rank0's own unrelated output state —
        # a hover-content mismatch, not just a cosmetic gap (see
        # multilevel-trajectory-plot-notes.md Phase 22 for the full
        # diagnosis). Filled in by the "parallel fork" tagging block below;
        # maps a member's own call_id -> its 0-based dispatch-order rank
        # within its native parallel_group.
        _tool_branch_rank: dict[str, int] = {}
        # Same key (a member's own call_id) as _tool_branch_rank above, but
        # holding that member's native parallel_group's own fork_kind
        # ("tool"/"agent") — used so the per-crec loop below can tag this
        # member's own is_branch_end directly (mirroring is_branch_begin)
        # without re-deriving fork_kind, and without tagging a delegation
        # (fork_kind=="agent") member here — that side already has its one
        # authoritative marker from the WAIT/RESUME handling instead.
        _tool_branch_kind: dict[str, str] = {}
        # Same key again, holding this member's own native parallel_group's
        # group_id and size — used to stamp parallel_group_id/parallel_size
        # directly on this branch's own is_branch_begin/is_branch_end states
        # (below), so branch-begin/branch-end nodes can be correlated back
        # to their fork/join group exactly like the fork/join states
        # themselves already are, instead of carrying no group identity at
        # all.
        _tool_branch_gid: dict[str, str] = {}
        _tool_branch_size: dict[str, int] = {}
        # This member's OWN dispatch-order entry ts (same value used to
        # build fork_branch_ts below) — recorded per call_id so that once
        # this SAME member's own is_branch_end is tagged later in the
        # per-crec loop, the two can be cross-linked bidirectionally
        # (branch_partner_ts), mirroring the Agents lane's own begin<->end
        # pairing above. Keyed by call_id (never by ts/rank order alone) so
        # a same-agent tool fan-out whose members finish out of dispatch
        # order still pairs each begin with its own true end.
        _tool_branch_entry_ts: dict[str, float] = {}
        # Same group_id as above, but for join_fork_ts ("Back to fork" popup
        # link) — the delegation-fork finalization pass sets this for agent
        # forks (_fork_ts_by_group); native parallel_group forks/joins are
        # tagged directly in this same loop instead (see the "parallel fork"/
        # "parallel aggregation" branches below), so they need their own
        # small group_id -> fork_ts map here.
        _native_fork_ts_by_gid: dict[str, float] = {}

        for j, crec in enumerate(call_sequence):
            ct_j   = crec.get("call_type", "")
            end_ts = crec["end_ts"] if crec.get("end_ts", 0) > 0 else t_max
            # Every call type (including ProcessingCall) gets its own state pair.
            # ProcessingCall is now a real bar so the prompt-assembly step is
            # visible as a distinct chain node (S_k → ⚙prompt → S_{k+1} → llm → …).
            # Truly instant calls (ToolCall/MemoryCall/RAGQuery with duration≈0)
            # use is_instant=True and are rendered as icon badges by the HTML renderer.
            start_hover = crec.get("input", "")
            if ct_j == "LLMCall" and j > 0 and call_sequence[j - 1].get("call_type") == "ProcessingCall":
                # The shared state after a context-assembly step already
                # carries the assembled prompt via CPR. Reusing the LLM input
                # here would show the same content twice on the same state.
                start_hover = ""
            # WAIT/RESUME markers are explicit boundaries; ensure their start
            # timestamp exists as a concrete state in-lane so Wn/Rn placement
            # stays stable. For regular calls, preserve the historical behavior
            # to avoid inserting synthetic connector transitions.
            if ct_j == "ProcessingCall" and str(crec.get("processing_type") or "") == PROCESSING_TYPE_WAIT_STATE:
                if str(crec.get("wait_role") or "").strip().upper() == "RESUME":
                    # A RESUME record's own ts and the real delegated reply's
                    # own completion ts (the preceding state, carrying the
                    # actual reply content) both represent the same
                    # real-world instant — off by a hair because they come
                    # from independent event sources (native observability's
                    # wait_state bookkeeping vs. the delegate's own
                    # LLMCall/execution_end). WAIT/RESUME are pure control
                    # boundaries, not processing steps of their own — bridge
                    # invisibly (default "BranchLink"), same as the WAIT side
                    # (see `connector_only` below), and snap to the Agents
                    # lane's own already-registered boundary for this same
                    # instant so both lanes share one StateNode and render
                    # perfectly aligned instead of a hair apart.
                    # A RESUME's own snapped ts frequently lands EXACTLY on
                    # the delegate's own last real state (its final
                    # LLMCall's own end state, already carrying the
                    # delegate's real final-answer content — same
                    # real-world instant, independent event sources). R must
                    # stay a plain, content-free boundary marker (that's the
                    # existing state's job — a real, distinct box showing the
                    # delegate's actual return content, exactly like any
                    # other content state) — so when the snapped ts would
                    # reuse that same content-bearing node, mint R its own
                    # fresh tick a hair after it instead of colliding onto
                    # it, the same forward-only technique tree.py's
                    # ``_reserve_marker_width``/``_bridge_to_state``'s own
                    # DFS-regression guard already use elsewhere to keep two
                    # genuinely different boundary roles from collapsing
                    # onto one timestamp.
                    _resume_target_ts = _snap_to_agent_lane_ts(crec["start_ts"])
                    _pre = elems[-1] if elems else None
                    if (
                        isinstance(_pre, StateNode)
                        and _pre.cpr_data
                        and abs(_pre.ts - _resume_target_ts) <= _TS_TOL
                    ):
                        _fresh_ts = _pre.ts + 1e-3
                        while _fresh_ts in state_reg:
                            _fresh_ts += 1e-3
                        _resume_target_ts = _fresh_ts
                    st_start = _bridge_to_state(
                        elems, _resume_target_ts, start_hover, "call",
                    )
                else:
                    st_start = _bridge_to_state(elems, crec["start_ts"], start_hover, "call")
            else:
                _own_rank = _tool_branch_rank.get(str(crec.get("call_id") or ""))
                if (
                    _own_rank is not None
                    and elems and isinstance(elems[-1], StateNode)
                ):
                    # ANY sibling (including rank0) of a genuine native
                    # parallel_group: bridge (BranchLink, same as the Agents
                    # lane's own rank>0 handling) so this branch's own real
                    # start state actually lands in `elems` instead of
                    # silently falling through — without this, the box
                    # immediately before this crec's own trans is whatever
                    # the previous sibling (or the fork marker itself) last
                    # left behind, a content mismatch, not this branch's own
                    # true starting context.
                    #
                    # rank0 needs this too, same as rank>0: tree.py's
                    # `_make_call_sequence` now splits a native parallel_group
                    # into two zero-duration markers (fork half, then this
                    # group's own branch members, then join half — see its
                    # docstring), so rank0's own real entry ts is a genuinely
                    # DIFFERENT timestamp from the fork marker's own ts, not
                    # a coincidence (verified against a real trace: the
                    # fork's own dispatch ts and rank0's own first real call
                    # ts are a few ms apart, never equal) — without bridging
                    # here too, rank0's own branch-begin box was silently
                    # missing (reported regression: "i see branch end but no
                    # branch start" / state numbering gap).
                    #
                    # Always bridge here — do NOT gate this behind a
                    # "> _TS_TOL" real-time-gap check. Genuinely concurrent
                    # branches (that's the whole point of a native
                    # parallel_group) routinely start/finish within a few
                    # milliseconds of each other, far under _TS_TOL's 50ms
                    # coalescing threshold — gating the bridge on a >50ms gap
                    # silently skipped it for virtually every real-world
                    # parallel dispatch (only ever exercised in prior testing
                    # via synthetic repros with artificially large gaps),
                    # which is why branch-begin markers were missing for true
                    # concurrent tool calls in production traces.
                    # _bridge_to_state itself already no-ops safely (returns
                    # the existing node, no duplicate box) when target_ts
                    # truly coincides with elems[-1]'s own ts, so removing the
                    # gap gate carries no risk of spurious duplication.
                    st_start = _bridge_to_state(elems, crec["start_ts"], start_hover, "call")
                    st_start.is_branch_begin = True
                    _own_cid = str(crec.get("call_id") or "")
                    st_start.parallel_group_id = _tool_branch_gid.get(_own_cid, "")
                    st_start.parallel_size = _tool_branch_size.get(_own_cid, 0)
                elif crec.get("_pg_fork_marker") or crec.get("_pg_join_marker"):
                    # A native parallel_group's fork/join marker (see
                    # tree.py's `_make_call_sequence` docstring) is a
                    # zero-duration bookkeeping event that, in the common
                    # case, lands on the EXACT same real ts as the state
                    # already sitting at the tail of `elems` (the preceding
                    # call's own end, or — for join — the last branch-end
                    # bridged in). Bridging (not a plain `state()` lookup)
                    # lets that common case fall through to `_bridge_to_state`'s
                    # own `node is prev: return node` short-circuit — reusing
                    # that SAME StateNode (no new box) instead of minting a
                    # redundant, almost-empty one right next to it purely to
                    # carry the F/J role tag (reported: "why do we have S16
                    # AND S17?" / "merge hyperlink should point to fork, it
                    # points to S16" — both were symptoms of that redundant
                    # split, not of the cross-link data itself, which was
                    # already correct). `_pg_marker_is_reuse` below then
                    # suppresses this iteration's own `_tr`/`st_end` appends
                    # so nothing else gets duplicated into `elems` either.
                    # When there IS a genuine real-time gap (no collision),
                    # `_bridge_to_state` mints its own fresh bridged state
                    # exactly like the branch-begin case above.
                    st_start = _bridge_to_state(elems, crec["start_ts"], start_hover, "call")
                else:
                    st_start = state(crec["start_ts"], start_hover, "call")
            _pg_marker_is_reuse = bool(
                (crec.get("_pg_fork_marker") or crec.get("_pg_join_marker"))
                and elems and elems[-1] is st_start
            )
            st_start.hover_by_lane["calls"] = crec.get("input", "")
            st_start.agent_id = str(crec.get("agent_id") or "")
            _tr = trans(crec, lane_level="call", node_id=f"tr-call-{j}",
                        start_ts=st_start.ts, end_ts=end_ts)
            # Native telemetry only for the PRE-call state: attach CPR content
            # exactly as emitted by telemetry, no remap/synthesis/append here.
            # (The POST-call state, st_end below, is a different concern: it
            # carries the LLM's own real output forward as one new fact — see
            # the dedicated comment there.)
            if ct_j == "LLMCall" and _tr.cpr_data and not st_start.cpr_data:
                st_start.cpr_data = list(_tr.cpr_data)
                st_start.model = _tr.model
            if not _pg_marker_is_reuse:
                elems.append(_tr)
            own_out  = crec.get("output", "")
            # ProcessingCall outputs are operation summaries ("3 injections · 51
            # tok") — they belong on the transition bar, not on the following
            # state.  Clear them so the state can show cumulative context instead.
            if ct_j == "ProcessingCall":
                own_out = ""
            next_rec  = call_sequence[j + 1] if j + 1 < len(call_sequence) else None
            next_inp  = next_rec.get("input", "") if next_rec else ""
            end_hover = own_out if ct_j == "ProcessingCall" else (own_out or next_inp)
            # A tool-turn LLM call emits no text (the model returned a tool
            # call, captured as the next bar), so its end state would be empty.
            # Show what the call led to instead of a bare "(no content)".
            if not end_hover and next_rec and ct_j != "ProcessingCall":
                _nl = next_rec.get("label") or _PROC_TYPE_LABEL.get(
                    next_rec.get("call_type", ""), next_rec.get("call_type", "")
                )
                if _nl:
                    end_hover = f"→ {_nl}"
            # Use "call_end" level (priority 4 > call=3) so this call's own
            # real output is the authoritative content for the state box at
            # this boundary — not silently overwritten by whatever record
            # comes next in call_sequence and happens to share this exact ts
            # (confirmed via a real trace: a native parallel_group branch's
            # own tool-result end state could get its real JSON output
            # replaced by the immediately-following context-assembly step's
            # generic label once both landed on the same ts).
            _end_level = "call_end" if own_out else "call"
            st_end = state(end_ts, end_hover, _end_level)
            st_end.hover_by_lane["calls"] = end_hover
            st_end.agent_id = str(crec.get("agent_id") or "")
            # This crec's own end-of-branch marker, if it's a member of a
            # same-agent native parallel_group (fork_kind=="tool"). Tagged
            # directly here — at the point this member's own st_end is
            # guaranteed to already exist — rather than via a lookback from
            # the group's own merged fork/aggregation record (which is
            # positioned EARLIER in call_sequence, before any of its own
            # members have been processed yet, so a state_reg lookup for a
            # member's own end_ts from there always misses; see
            # is_branch_begin's own analogous fix above). A delegation
            # (fork_kind=="agent") member is skipped — its one authoritative
            # boundary marker already comes from the WAIT/RESUME handling.
            if _tool_branch_kind.get(str(crec.get("call_id") or "")) == "tool":
                st_end.is_branch_end = True
                _own_cid = str(crec.get("call_id") or "")
                st_end.parallel_group_id = _tool_branch_gid.get(_own_cid, "")
                st_end.parallel_size = _tool_branch_size.get(_own_cid, 0)
                # Cross-link this branch's own begin<->end pair bidirectionally
                # (branch_partner_ts), by call_id — never by ts-proximity,
                # since concurrent branches can legitimately finish out of
                # dispatch order. Mirrors the Agents lane's own pairing.
                _entry_ts = _tool_branch_entry_ts.get(_own_cid)
                if _entry_ts is not None:
                    st_end.branch_partner_ts = _entry_ts
                    _entry_state = state_reg.get(_entry_ts)
                    if _entry_state is not None:
                        _entry_state.branch_partner_ts = st_end.ts
            if ct_j == "LLMCall" and not st_end.cpr_data:
                # The LLM's own generated output (a tool-call decision or a
                # final text reply) is a genuine, real fact that becomes part
                # of context for whatever comes next — even when no
                # downstream native ``context_part_contributed``/
                # ``context_assembled`` cycle ever re-observes it (e.g. a
                # moderator that delegates once and never calls itself
                # again, so telemetry never gets a chance to record its own
                # decision as an assembled context part for a subsequent
                # call). Reuse the exact same real content
                # ``llm_tool_decisions``/``crec["output"]`` already derive
                # from native telemetry (never fabricated — see
                # ``_collect_llm_tool_decisions``'s docstring), tagged with
                # the SAME ``assembled/tool_call`` source native telemetry
                # itself uses for an assistant-role context part (verified
                # against a real trace in ``_collect_context_provenance``),
                # so it renders identically — 3rd-position ordering,
                # category, highlight color — to a natively-observed
                # occurrence of this very same kind of fact. Cumulative: it
                # carries forward everything already assembled at
                # ``st_start`` plus this one new fact, so the diff for this
                # LLM transition shows exactly one addition.
                _own_llm_out = str(crec.get("output") or "").strip()
                if not _own_llm_out:
                    _own_llm_out = llm_tool_decisions.get(crec.get("call_id", ""), "")
                if _own_llm_out:
                    _own_entry = {
                        "source": "assembled/tool_call",
                        "category": _source_category("assembled/tool_call", "inject"),
                        "mechanism": "inject",
                        "retrieval": "",
                        "decision": "",
                        "causeType": "llm_decision",
                        "cause": "LLM tool-call decision",
                        "trigger": "llm_tool_call",
                        "actor": "llm",
                        "via": "llm_generated",
                        "tokens": max(1, len(_own_llm_out) // 4),
                        "retained": True,
                        "placement": "assembled/2/assistant",
                        "content": _own_llm_out,
                    }
                    st_end.cpr_data = list(st_start.cpr_data) + [_own_entry]
                    st_end.model = st_start.model
            if ct_j == "ProcessingCall" and str(crec.get("processing_type") or "") == PROCESSING_TYPE_WAIT_STATE:
                _wid = str(crec.get("wait_link_id") or f"wait-{crec.get('call_id', j)}")
                _role = str(crec.get("wait_role") or "").strip().upper()
                _note = str(crec.get("wait_note") or "").strip()
                _wf_rep = _wait_cluster_rep.get(_wid, _wid)
                _wf_is_cluster = _wf_rep in _wait_fork_branch_ts
                _wf_is_rep = _wf_rep == _wid
                if _role in {"WAIT", "RESUME"}:
                    _target = st_start
                    if _role == "RESUME" and _target.wait_role == "WAIT" and abs(st_end.ts - st_start.ts) > _TS_TOL:
                        _target = st_end
                    # Every cluster member still gets the FULL WAIT/RESUME
                    # tagging (wait_link_id/wait_role/label/_attach_wait_meta)
                    # — mirroring to the Agents lane and every other existing
                    # invariant depend on it. Members SHARE one number (see
                    # the wait-fork clustering above, keyed by _wf_rep), and
                    # only the representative's state gets the is_fork/
                    # is_join badge (with every member's real ts folded into
                    # its forkBranchTs/join_of). A non-representative
                    # member's own box is then hidden below (connector_only)
                    # instead of rendering its own redundant W/R box next to
                    # the representative's — same idea as a native
                    # parallel_group fork/join, just for concurrent
                    # delegation waits.
                    _target.wait_link_id = _wid
                    _target.wait_role = _role
                    _target.wait_note = _note
                    _target.label_override = _label_for_wait(_role, _wid)
                    _target.suppress_cpr = True
                    _target.cpr_data = []
                    _attach_wait_meta(_target, _wid=_wid, _role=_role, _note=_note)
                    if _wid in _native_wait_gid and _role == "WAIT":
                        # This branch's own dispatch boundary on the Calls
                        # lane — the delegation-fork counterpart of the
                        # native tool-level parallel_group's own
                        # is_branch_begin tagging (see Phase 22). The
                        # matching is_branch_end lands on the RESUME side's
                        # own real completion state (st_end), below.
                        _target.is_branch_begin = True
                    if _wf_is_cluster:
                        if _wf_is_rep:
                            # If the Agents lane's own fork/join pass (see
                            # is_fork/is_join above, built from
                            # _detect_delegation_forks's native
                            # parallel_group facts) already tagged this
                            # exact shared StateNode, its parallel_group_id/
                            # parallel_size/fork_branch_ts/join_of are more
                            # complete (real per-branch navigation ts) —
                            # leave them untouched rather than overwriting
                            # with this cluster's own "waitfork-" id, which
                            # would otherwise leave the fork state and its
                            # matching join state with two DIFFERENT
                            # parallelGroupId values for the very same
                            # concurrent delegation.
                            if _role == "WAIT" and not _target.is_fork:
                                _target.parallel_group_id = f"waitfork-{_wf_rep}"
                                _target.parallel_size = len(_wait_cluster_members[_wf_rep])
                                _target.is_fork = True
                                _target.fork_branch_ts = _wait_fork_branch_ts[_wf_rep]
                                # Wait-clusters are only ever built for
                                # wait_scope=="delegation" (see the guard at
                                # the top of this cluster-building pass) —
                                # always a genuine cross-agent fork.
                                _target.fork_kind = "agent"
                            elif _role != "WAIT" and not _target.is_join:
                                _target.parallel_group_id = f"waitfork-{_wf_rep}"
                                _target.parallel_size = len(_wait_cluster_members[_wf_rep])
                                _target.is_join = True
                                _target.join_of = list(_wait_cluster_members[_wf_rep])
                                _target.fork_kind = "agent"
                        else:
                            _target.is_connector_only = True
                    # Keep WAIT/RESUME boundaries prose-free in lane hover text.
                    # R1 itself stays symmetrical to W1 (a plain boundary
                    # marker, no content) — the tool's real completion is
                    # instead shown as a REPEAT of the blocking tool-call
                    # node right after R1 (mirrors the original dispatch
                    # bar earlier in this lane, now representing it
                    # finishing), followed by a state carrying the tool's
                    # real result content. This re-presents the purely
                    # mechanical "resume caller" bridge (_tr/st_end, built
                    # generically above from this same wait_state crec) as
                    # that real event instead of a hidden bookkeeping note.
                    # Runs for EVERY cluster member (representative or not)
                    # — each branch's own real reply is genuine content, only
                    # the redundant W/R control box above is collapsed.
                    if _role == "RESUME":
                        _wait_rec_for_result = _wait_records.get(_wid, {}).get("WAIT")
                        _blocking_result = (
                            _blocking_tool_for_wait(_wait_rec_for_result)
                            if _wait_rec_for_result else None
                        )
                        _result_output = str((_blocking_result or {}).get("output") or "").strip()
                        if _blocking_result and _result_output:
                            _result_tool_name = str(_blocking_result.get("tool_name") or "")
                            _result_input = str(_blocking_result.get("input") or "").strip()
                            # Only repurpose the bridging Trans (_tr) into a
                            # visible ToolCall icon when st_end is a
                            # genuinely DISTINCT state from R3 itself
                            # (st_start). A WAIT/RESUME marker crec always
                            # has start_ts == end_ts (an instantaneous
                            # event) — and `state()` keys its registry
                            # purely by ts, so when no ts-collision-avoidance
                            # bump was needed for st_start, `state(end_ts)`
                            # below returns the SAME object as st_start, not
                            # a fresh one. Flipping _tr visible in that case
                            # would render this ToolCall icon sandwiched
                            # between two renderings of that one shared R
                            # marker — i.e. a tool call appearing to sit
                            # inside the Resume node. Never do that: keep
                            # _tr a plain invisible connector and only
                            # attach the real content as backing (hover/
                            # popup) data on the shared state below.
                            if st_end is not st_start:
                                _tr.call_type = "ToolCall"
                                _tr.label = _result_tool_name or _tr.label
                                _tr.connector_only = False
                                _tr.is_instant = True
                                _tr.hover_in = _result_input
                                _tr.hover_out = _result_output
                            st_end.cpr_data = [{
                                "source": "tool/output",
                                "category": "TOOL",
                                "mechanism": "append",
                                "retrieval": "",
                                "decision": "",
                                "causeType": "tool_result",
                                "cause": "tool execution result",
                                "trigger": "tool_execution",
                                "actor": "tool",
                                "via": "tool_provided",
                                "tokens": max(1, len(_result_output) // 4),
                                "retained": True,
                                "placement": "tool/output",
                                "content": _result_output,
                            }]
                            st_end.hover = _result_output
                            st_end.hover_by_lane["calls"] = (
                                f"→ {_result_tool_name}" if _result_tool_name
                                else st_end.hover_by_lane.get("calls", "")
                            )
                        if _wid in _native_wait_gid:
                            # This branch's own end-of-branch boundary on
                            # the Calls lane — st_end here is the real
                            # content state carrying the delegate's own
                            # completion (or, if no blocking result was
                            # found, still the RESUME's own bridged state) —
                            # either way the natural point right before the
                            # scoped reset separator to the next branch (or
                            # the aggregation step) kicks in.
                            st_end.is_branch_end = True
            elif ct_j == "ProcessingCall" and str(crec.get("processing_type") or "") == "parallel_group":
                # A fork IS a wait-many, a join IS a resume-many: tag the
                # bracketing state exactly like WAIT/RESUME (see above) so
                # the renderer gives it the same control-boundary treatment,
                # instead of a colored processing bar. This is a native fact
                # ("parallel fork"/"parallel aggregation" markers), not
                # specific to agent-delegation forks — a single agent's own
                # parallel tool calls carry the same processing_type. If the
                # agents-lane fork/join pass already tagged this exact shared
                # StateNode (delegation forks are tagged there first — see
                # is_fork/is_join above), leave it untouched rather than
                # overwriting a stricter branch-navigation-aware tagging with
                # a poorer one.
                #
                # fork_kind: whether this native group's own members are
                # further AgentCall delegations (a real cross-agent fork,
                # branches leave this agent's row entirely) or plain leaf
                # ToolCalls (this ONE agent's own N tool calls dispatched at
                # once — branches never leave this agent's own row). Reported
                # regression: both used to render with IDENTICAL "Parallel
                # fork/join point, N branches" wording+badge, making a
                # same-agent tool fan-out visually indistinguishable from a
                # hand-off to other agents ("some splits are between agents,
                # some are within one agent" — impossible to tell apart).
                _pg_gid  = str(crec.get("group_id") or "").strip()
                _pg_size = int(crec.get("tool_count") or len(crec.get("tools") or []) or 0)
                _pg_tool_ids = [str(_t.get("call_id") or "") for _t in (crec.get("tools") or [])]
                _pg_kind = "agent" if any(
                    any(str(_child.get("level") or "") == "agent" for _child in children_of.get(_tid, []))
                    for _tid in _pg_tool_ids
                ) else "tool"
                # Dispatch-order rank (0-based) of every member — used both
                # to let the main per-crec loop above know which members
                # (rank>0) need `_bridge_to_state` instead of a plain
                # `state()` lookup, and to populate fork_branch_ts below (the
                # same per-branch navigation list the wait-cluster mechanism
                # already exposes for delegation forks — see `_label_for_wait`
                # callers/`_waitSummaryHtml` in multilevel.html).
                _pg_ranked = sorted(
                    _pg_tool_ids,
                    key=lambda _tid: float((rec_by_id.get(_tid) or {}).get("start_ts") or 0.0),
                )
                # tree.py's `_make_call_sequence` now splits this native
                # parallel_group into two zero-duration markers around its
                # own branch members (fork half, then the branches, then
                # join half — see its docstring: fixes "merge before the
                # branches" by giving the join half a later DFS position
                # than every branch it aggregates). Both halves still carry
                # the SAME call_id/processing_name ("parallel fork" — the
                # runtime never emits an independent "parallel aggregation"
                # record of its own), so `_pg_fork_marker`/`_pg_join_marker`
                # (stamped by tree.py) are what tell the two apart here, not
                # `_pg_name`.
                _is_fork_marker = bool(crec.get("_pg_fork_marker"))
                _is_join_marker = bool(crec.get("_pg_join_marker"))
                if _is_fork_marker:
                    for _rank, _tid in enumerate(_pg_ranked):
                        _tool_branch_rank[_tid] = _rank
                        _tool_branch_kind[_tid] = _pg_kind
                        _tool_branch_gid[_tid] = _pg_gid
                        _tool_branch_size[_tid] = _pg_size
                        _tool_branch_entry_ts[_tid] = float((rec_by_id.get(_tid) or {}).get("start_ts") or 0.0)
                if _is_fork_marker:
                    # is_fork/fork_branch_ts on the CALLS lane's own shared
                    # state is only meaningful for a same-agent tool fan-out
                    # (fork_kind=="tool") — for a cross-agent delegation
                    # fork (fork_kind=="agent"), the Agents lane already
                    # carries the one authoritative fork badge (built from
                    # this exact native fact, see _parallel_info above), and
                    # this native ProcessingCall record's own ts on the Calls
                    # lane frequently does NOT coincide with that state
                    # (independent event sources/bookkeeping, a few ms
                    # apart) — tagging it again here would render a SECOND,
                    # redundant fork badge at a different chart column for
                    # the very same fork. "delegation should be similar
                    # except the fork and merge nodes are at agent level"
                    # (explicit user directive) — so skip this entirely for
                    # fork_kind=="agent"; the Calls lane's own per-branch
                    # story for delegation is told by the WAIT/RESUME
                    # is_branch_begin/is_branch_end markers instead (see the
                    # wait_state handling above).
                    if _pg_kind == "tool" and not st_start.is_fork:
                        st_start.is_fork           = True
                        st_start.parallel_group_id = _pg_gid
                        st_start.parallel_size     = _pg_size
                        st_start.fork_kind         = _pg_kind
                        st_start.fork_branch_ts    = [
                            float((rec_by_id.get(_tid) or {}).get("start_ts") or 0.0)
                            for _tid in _pg_ranked
                        ]
                    if _pg_kind == "tool" and _pg_gid not in _native_fork_ts_by_gid:
                        _native_fork_ts_by_gid[_pg_gid] = st_start.ts
                    # Branch-begin markers for every member (rank0 included)
                    # are now tagged directly in the main per-crec loop above
                    # (via `_tool_branch_rank`, extended to cover rank0 too —
                    # see the bridging block's own comment), at the point
                    # each member's own st_start is bridged into `elems` —
                    # not here. A lookback from here used to try to tag
                    # rank0 by assuming its own start state already existed
                    # at this point in the walk (it doesn't: tree.py's fork
                    # marker is its own separate zero-duration boundary now,
                    # a genuinely different ts from any branch member's own
                    # entry — verified against a real trace) and was a
                    # silent no-op for every rank regardless.
                # The aggregation ("join") half of this SAME native
                # parallel_group — tree.py's `_make_call_sequence` now
                # visits this group's own fork marker and join marker in two
                # SEPARATE per-crec loop iterations (`_is_fork_marker`/
                # `_is_join_marker` above), with every branch member's own
                # crec processed in between — so this half only ever runs
                # once this group's own join marker (not the fork marker)
                # reaches this point in the walk.
                #
                # Same asymmetry as the fork side above: is_join/join_of
                # on the CALLS lane's own shared state is only tagged
                # for a same-agent tool fan-out; a delegation join's one
                # authoritative badge already lives on the Agents lane.
                if _is_join_marker and _pg_kind == "tool" and not st_end.is_join:
                    st_end.is_join           = True
                    st_end.parallel_group_id = _pg_gid
                    st_end.parallel_size     = _pg_size
                    st_end.fork_kind         = _pg_kind
                    st_end.join_of           = list(_pg_tool_ids)
                    if _pg_gid in _native_fork_ts_by_gid:
                        st_end.join_fork_ts = _native_fork_ts_by_gid[_pg_gid]
                        # Forward cross-link (mirrors join_fork_ts above): the
                        # fork state now knows its own matching join's ts
                        # too, so fork<->join are cross-linked bidirectionally.
                        _fork_state = state_reg.get(_native_fork_ts_by_gid[_pg_gid])
                        if _fork_state is not None:
                            _fork_state.join_ts = st_end.ts
                # Each member's own is_branch_end is tagged directly in the
                # main per-crec loop above (via _tool_branch_kind), at the
                # point its own st_end is created.
                # The aggregation's own summary ("assembly part") only
                # belongs on the join marker's own st_end — the fork
                # marker's own crec carries the SAME copied `output` text
                # (tree.py's split copies the whole dict for both halves),
                # and st_start==st_end for the fork marker's own
                # zero-duration iteration, so gate this on the join marker
                # specifically to avoid stamping the aggregation summary
                # onto the fork state instead.
                if _is_join_marker and not st_end.assembly_note:
                    st_end.assembly_note = str(crec.get("output") or "").strip()
                # This marker is real, visible work now (connector_only
                # was flipped off above, in trans()'s own condition) —
                # but native "parallel aggregation" records are typically
                # near-instantaneous (bookkeeping, not an LLM/tool call
                # with its own duration), so force the icon-badge
                # rendering the HTML renderer already uses for other
                # zero-duration events, same as the WAIT/RESUME bridge
                # above, instead of leaving it as an invisible
                # zero-width bar.
                if abs(_tr.end_ts - _tr.start_ts) <= _TS_TOL:
                    _tr.is_instant = True
            if not _pg_marker_is_reuse:
                elems.append(st_end)
        for el in elems:
            if isinstance(el, StateNode) and el.cpr_data and not el.model:
                _src_model = next(
                    (
                        _t.model for _t in elems
                        if isinstance(_t, TransNode) and _t.call_type == "LLMCall" and _t.model
                    ),
                    "",
                )
                el.model = _src_model
        # NOTE: agent-boundary transitions in this lane no longer need their
        # own marker — the delegation tool call is now a visible node here
        # (see tree.py's _reserve_marker_width) and the DFS branch-reset
        # separator (is_branch_reset, stamped in the finalization pass below)
        # already marks every "enter a new sibling" boundary. A "return to
        # the parent and continue" transition (delegate's last call ->
        # delegating agent's next call) isn't itself entering a new branch,
        # so it renders plainly — the Agents lane already shows which agent
        # owns which span.

        call_lane.sequence = elems
        lanes.append(call_lane)

        # Mirror WAIT->next-numbered-state boundaries on the Agents lane so
        # Wn is followed by the same numbered state on both lanes.
        if "agent_lane" in locals() and agent_lane.sequence:
            def _insert_agent_boundary(_ts: float, *, mark_left_connector: bool = False, exact: bool = False) -> StateNode | None:
                _states = [el for el in agent_lane.sequence if isinstance(el, StateNode)]
                _existing = next((s for s in _states if abs(_ts - s.ts) <= 1e-9), None)
                if _existing is not None:
                    return _existing
                _seq = agent_lane.sequence
                for _i in range(1, len(_seq) - 1):
                    _prev = _seq[_i - 1]
                    _cur = _seq[_i]
                    _nxt = _seq[_i + 1]
                    if not isinstance(_prev, StateNode):
                        continue
                    if not isinstance(_cur, TransNode):
                        continue
                    if not isinstance(_nxt, StateNode):
                        continue
                    if not (_prev.ts + 1e-9 < _ts < _nxt.ts - 1e-9):
                        continue
                    if not (_cur.start_ts - _TS_TOL <= _ts <= _cur.end_ts + _TS_TOL):
                        continue
                    # If the requested boundary lands within noise distance
                    # of the trans's own end, reuse the ALREADY EXISTING next
                    # state instead of slicing off a near-zero-width trailing
                    # remainder of the delegate's own fragment. That
                    # remainder is an alignment artefact (the wait_state
                    # RESUME record's own ts vs. the delegate AgentCall
                    # record's own recorded end_ts — both meant to represent
                    # the exact same "reply available, resume caller"
                    # instant, off by a hair because they come from
                    # independent event sources), not a genuine extra
                    # activity span — stranding it as its own tiny node
                    # between the resume marker and the delegating agent's
                    # next real fragment would show the resumed agent
                    # continuing one node too late. Same idea, mirrored, for
                    # the (unobserved so far, but symmetric) case where the
                    # boundary lands within noise distance of the trans's own
                    # start.
                    # Never reuse a state that already carries real content
                    # (cpr_data) as the mirrored WAIT/RESUME boundary, even
                    # within tolerance — that content is the delegate's own
                    # genuine return value and must keep rendering as its own
                    # distinct, content-bearing box (see the Calls lane's own
                    # matching guard against this same collision).
                    #
                    # Never reuse a state reached via a "BranchLink" bridge
                    # either (see _bridge_to_state) — that reuse-fallback is
                    # meant to catch a genuine same-instant alignment hair,
                    # not the Agents lane's own unrelated DFS-position-
                    # regression bridging artefact (a stale, unreconciled
                    # agent_sequence fragment boundary that happens to land
                    # within _TS_TOL of this wid purely by coincidence — the
                    # "hair behind" gap documented above call_sequence's own
                    # construction). Reusing it here would silently relabel
                    # an unrelated synthetic bridge target as this WAIT/
                    # RESUME boundary instead of creating (or reusing) the
                    # one actually at _ts.
                    _prev_bridge = _seq[_i - 2] if _i - 2 >= 0 else None
                    _prev_is_bridge_artifact = (
                        isinstance(_prev_bridge, TransNode) and _prev_bridge.call_type == "BranchLink"
                    )
                    _nxt_bridge = _seq[_i + 2] if _i + 2 < len(_seq) else None
                    _nxt_is_bridge_artifact = (
                        isinstance(_nxt_bridge, TransNode) and _nxt_bridge.call_type == "BranchLink"
                    )
                    # "Noise distance" here means the same real-world instant
                    # recorded a hair apart by two independent event sources
                    # (a fraction of a millisecond, e.g. a delegate's own
                    # recorded end_ts vs. a RESUME record's own ts) — NOT
                    # _TS_TOL's full 50ms, which is meant for a different
                    # purpose (matching WAIT/RESUME across genuinely
                    # different clock sources) and is wide enough to reuse
                    # some unrelated Agents-lane state several ms away.
                    # `exact=True` callers (the WAIT/RESUME boundary itself,
                    # see the call site below) skip this fallback entirely —
                    # that boundary is explicitly meant to land at the exact
                    # same ts as its Calls-lane counterpart so both lanes
                    # share one bucket/x-axis column (reported: "W1/W2 nodes
                    # are not aligned" — a nearby-but-different Agents-lane
                    # state was being reused instead of minting fresh at the
                    # true, shared ts). `exact=False` callers (mirroring the
                    # NEXT numbered state after a WAIT, a different use case
                    # that only wants to avoid a redundant near-duplicate
                    # box) keep the hair-distance reuse.
                    _HAIR_TOL = 1e-3
                    if not exact:
                        if (abs(_ts - _nxt.ts) <= _HAIR_TOL and not _nxt.wait_link_id
                                and not _nxt.cpr_data and not _nxt_is_bridge_artifact):
                            return _nxt
                        if (abs(_ts - _prev.ts) <= _HAIR_TOL and not _prev.wait_link_id
                                and not _prev.cpr_data and not _prev_is_bridge_artifact):
                            return _prev
                    _mid = state(_ts)
                    # The slice before the mirrored boundary (e.g. between a
                    # WAIT state and the delegated-input state that follows
                    # it) is a bridging artefact, not a genuine independent
                    # activity span — mark it connector_only so the renderer
                    # draws no bar/label for it (still a real TransNode, so
                    # the state/transition alternation invariant holds).
                    _left = replace(
                        _cur, node_id=f"{_cur.node_id}-l", end_ts=_ts,
                        connector_only=(mark_left_connector or _cur.connector_only),
                    )
                    _right = replace(_cur, node_id=f"{_cur.node_id}-r", start_ts=_ts)
                    _seq[_i:_i + 1] = [_left, _mid, _right]
                    return _mid
                return None

            _call_states = [el for el in call_lane.sequence if isinstance(el, StateNode)]
            _wait_states = [
                s for s in _call_states
                if str(s.label_override or "").startswith(("W", "R")) and s.wait_link_id
            ]

            # Ensure WAIT/RESUME nodes exist on agents lane at the same ts and
            # carry the same wait metadata for same-lane W↔R linking.
            #
            # A RESUME's own split (when it doesn't already land on an
            # existing agent_sequence fragment boundary — the usual case,
            # since its ts was minted fresh in the Calls lane to dodge the
            # delegate's own content state, see the collision fix above)
            # leaves a tiny "left" sliver between the delegate's own real end
            # and this RESUME state. That sliver is a bridging artefact (the
            # RESUME's own ts vs. the delegate's own recorded end — same
            # real-world instant, off by a hair), never genuine activity of
            # the resuming (delegating) agent — mark it connector_only so
            # _computeCollapsibleGroups (multilevel.html) doesn't fold it
            # into the delegating agent's own run, which would silently
            # anchor that run's start boundary on the delegate's own content
            # state instead of on this RESUME marker (violating the
            # "interrupted agent's new occurrence starts with RESUME"
            # invariant). A WAIT's own left sliver, by contrast, IS the
            # agent's own genuine final activity before being interrupted —
            # must stay a real, visible trans.
            for _ws in _wait_states:
                _ag = _insert_agent_boundary(
                    float(_ws.ts), mark_left_connector=(_ws.wait_role == "RESUME"), exact=True,
                )
                if _ag is None:
                    continue
                _ag.wait_link_id = _ws.wait_link_id
                _ag.wait_role = _ws.wait_role
                _ag.wait_note = _ws.wait_note
                _ag.wait_meta = dict(_ws.wait_meta or {})
                _ag.label_override = _ws.label_override
                _ag.suppress_cpr = True
                _ag.cpr_data = []
                # Keep WAIT/RESUME boundaries prose-free in lane hover text.
                # A non-representative wait-fork cluster member (see the
                # clustering above) is hidden on the Calls lane too — mirror
                # that same hiding here so its Agents-lane counterpart
                # doesn't render its own redundant W/R box either.
                _wf_rep = _wait_cluster_rep.get(_ws.wait_link_id, _ws.wait_link_id)
                if _wf_rep in _wait_fork_branch_ts and _wf_rep != _ws.wait_link_id:
                    _ag.is_connector_only = True

            # Map each delegated agent fragment's own entry input (already
            # correctly extracted from its execution_start event, identical
            # to the dispatching tool call's own input) by the delegation
            # tool call that dispatched it (its parent_call_id). The "next
            # numbered state" mirrored onto the Agents lane right after a
            # delegation WAIT (this delegate's own entry point) shares its
            # StateNode object with the Calls lane (same ts → same
            # state_reg entry), so it starts out with only
            # hover_by_lane["calls"] set (e.g. "context assembly") and no
            # "agents" entry at all — the JS renderer then falls back to
            # whatever lane happens to be first in hoverByLane, leaking the
            # Calls lane's own label into the Agents-lane popup. Attach the
            # real delegation task text explicitly so it renders (and is
            # flagged NEW, since this is the first state in the Agents lane
            # for this agent_id) as its own CPR facet, matching the
            # dispatching tool call's input. Keep only the FIRST fragment
            # per parent_call_id (DFS order) — a delegate that itself
            # further delegates gets split into pre/tail fragments, and
            # only the pre (entry) fragment carries the real input.
            _delegate_input_by_tool_call_id: dict[str, str] = {}
            for _a in agent_sequence:
                _pcid = str(_a.get("parent_call_id") or "")
                if _pcid and _pcid not in _delegate_input_by_tool_call_id:
                    _delegate_input_by_tool_call_id[_pcid] = _a.get("input", "")

            for _idx, _st in enumerate(_call_states):
                if not str(_st.label_override or "").startswith("W"):
                    continue
                _next_numbered = next(
                    (s for s in _call_states[_idx + 1:] if not str(s.label_override or "")),
                    None,
                )
                if _next_numbered is not None:
                    # The span between the WAIT boundary and this numbered
                    # state is a hand-off gap, not the delegate agent already
                    # at work — see _insert_agent_boundary's mark_left_connector.
                    _mid = _insert_agent_boundary(float(_next_numbered.ts), mark_left_connector=True)
                    _tool_call_id = str((_st.wait_meta or {}).get("blocking", {}).get("toolCallId") or "")
                    _delegate_input = _delegate_input_by_tool_call_id.get(_tool_call_id, "")
                    if _mid is not None and _delegate_input and not _mid.cpr_data:
                        _mid.hover_by_lane["agents"] = _delegate_input
                        _mid.cpr_data = [{
                            "source": "assembled/user",
                            "category": _source_category("assembled/user", "inject"),
                            "mechanism": "inject",
                            "retrieval": "",
                            "decision": "",
                            "causeType": "delegated",
                            "cause": "delegation task",
                            "trigger": "delegation_dispatch",
                            "actor": "moderator",
                            "via": "delegation",
                            "tokens": max(1, len(_delegate_input) // 4),
                            "retained": True,
                            "placement": "assembled/user",
                            "content": _delegate_input,
                        }]

        # Do not inject every agent boundary in the Calls lane: those
        # connector-only buckets create visual gaps with no call transition.
        # Calls lane should primarily reflect call telemetry boundaries.
        call_ts: set[float] = {el.ts for el in elems if isinstance(el, StateNode)}

        # Blocked-action ghost markers: a BLOCK/TERMINATE/SKIP/BLACKLIST
        # decision stops the call before the engine ever runs it, so there is
        # no execution record to hang a bar on. WHOSE ghost this is (agent_id,
        # decision, reason, policy — see governance.py's _collect_blocked_actions)
        # is already fully known from the governance_decision event's own real
        # ids; the tolerance search below only decides which EXISTING visual
        # state to hang that already-identified ghost's hover/badge on, in the
        # absence of any execution record to attach it to directly — it never
        # guesses which call was blocked. Injects a connector-only marker
        # state when nothing already sits at that boundary — same technique
        # used above for agent-boundary timestamps.
        for ghost in blocked:
            gts = ghost["ts"]
            close_ts = next((ex for ex in call_ts if abs(gts - ex) <= _TS_TOL), None)
            if close_ts is not None:
                gstate = state_reg[close_ts]
            else:
                gstate = state(gts)
                call_lane.connector_only_ts.add(gts)
                call_lane.sequence.append(gstate)
                call_ts.add(gts)
            gstate.governance.append({
                "decision":   ghost["decision"],
                "reason":     ghost["reason"],
                "policyName": ghost["policyName"],
            })

    # ── Thinking lane: one ThinkingCall per LLM call that has thinking content
    thinking_records = _synthesize_thinking_records(records) if "thinking" in facets else []
    thinking_sub_tss: list[float] = []   # intermediate state ts values (for letter labels)

    # Build a map from LLM call_id → staggered start_ts so the thinking lane
    # anchors at the correct (post-stagger) timestamp.
    _staggered_llm_starts: dict[str, float] = {
        r["call_id"]: r["start_ts"]
        for r in call_sequence
        if r.get("call_type") == "LLMCall"
    }

    if thinking_records:
        thinking_lane = LaneDef("thinking", "thinking", "Thinking")
        telems: list = []
        prev_ts: float | None = None
        prev_agent_id: str | None = None

        for k, trec in enumerate(sorted(thinking_records, key=lambda r: r["start_ts"])):
            # Use staggered LLM call start_ts (parent_call_id points to the
            # LLMCall) so the thinking lane aligns with the call lane visually.
            _parent_cid = trec.get("parent_call_id", "")
            ts_start = _staggered_llm_starts.get(_parent_cid, trec["start_ts"])
            # Adjust thinking end proportionally to the staggered start.
            _orig_start = trec["start_ts"]
            _offset = ts_start - _orig_start
            ts_think_end = (trec["end_ts"] if trec.get("end_ts", 0) > 0 else t_max) + _offset
            ts_llm_end   = (trec.get("_llm_end_ts") or t_max)

            if prev_ts is None:
                # First thinking record — lane anchors at the LLM call (ts_start)
                _st_anchor = state(ts_start, trec.get("input", ""), "call")
                _st_anchor.hover_by_lane["thinking"] = trec.get("input", "")
                telems.append(_st_anchor)
            elif ts_start > prev_ts + _TS_TOL:
                _is_inter_agent = (prev_agent_id is not None and prev_agent_id != trec["agent_id"])
                # Add the anchor state; mark as laneRestart when agents differ
                # (breaks lifeline — thinking is per-agent, not continuous).
                # For same-agent gaps the lifeline connects them naturally.
                _st_gap_end = state(ts_start, trec.get("input", ""), "call")
                _st_gap_end.hover_by_lane["thinking"] = trec.get("input", "")
                if _is_inter_agent:
                    _st_gap_end.is_lane_restart = True
                telems.append(_st_gap_end)
            else:
                # The boundary state at ts_start already exists in state_reg
                # (registered by the call lane). Update its thinking hover and
                # append it to telems so the thinking lane renders a box there.
                _anch = state(ts_start)
                _anch.hover_by_lane["thinking"] = trec.get("input", "")
                telems.append(_anch)

            # ThinkingCall transition: from LLM call start → thinking end (85%)
            telems.append(trans(trec, lane_level="thinking",
                                node_id=f"tr-think-{k}",
                                call_type="ThinkingCall", label="Think",
                                start_ts=ts_start, end_ts=ts_think_end))
            think_st = state(ts_think_end, trec.get("output", ""), "thinking")
            think_st.hover_by_lane["thinking"] = trec.get("output", "")
            telems.append(think_st)
            thinking_sub_tss.append(ts_think_end)   # mark as letter-suffix candidate

            # Output passthrough: thinking end → LLM call end (remaining 15%)
            # Rendered as ThinkingEmit (indigo-900, label "Emit") so users can
            # distinguish it from the idle gap before thinking starts.
            if ts_llm_end > ts_think_end + _TS_TOL:
                emit_rec: dict = {"input": trec.get("output", ""), "output": trec.get("llm_output", ""), "agent_id": trec["agent_id"]}
                telems.append(trans(emit_rec, lane_level="thinking",
                                    node_id=f"tr-think-out-{k}",
                                    call_type="ThinkingEmit", label="Emit",
                                    start_ts=ts_think_end, end_ts=ts_llm_end))
                telems.append(state(ts_llm_end))
                prev_ts = ts_llm_end
            else:
                prev_ts = ts_think_end
            prev_agent_id = trec["agent_id"]

        # Lane ends at the state after the last Emit — no leading gap from t_min,
        # no trailing gap to t_max.

        thinking_lane.sequence = telems
        lanes.append(thinking_lane)

    # ── DFS virtual-position axis: stamp dfs_pos/branch_id onto every node ──
    # _ts_to_dfs_pos (from _assign_dfs_positions) only covers timestamps that
    # appear in call_sequence (the Calls lane) — extend it to every timestamp
    # in state_reg (session/MAS boundaries, HITL gaps, etc. share most of the
    # same timestamps via _align_record_boundaries, but not necessarily all)
    # by interpolating between the nearest covered neighbors in real time.
    _all_ts = sorted(state_reg.keys())
    _covered = sorted(_ts_to_dfs_pos)
    if _covered:
        for _ts in _all_ts:
            if _ts in _ts_to_dfs_pos:
                continue
            _lo = next((c for c in reversed(_covered) if c <= _ts), None)
            _hi = next((c for c in _covered if c >= _ts), None)
            if _lo is None:
                _ts_to_dfs_pos[_ts] = _ts_to_dfs_pos[_covered[0]] - (_covered[0] - _ts)
            elif _hi is None:
                _ts_to_dfs_pos[_ts] = _ts_to_dfs_pos[_covered[-1]] + (_ts - _covered[-1])
            elif _lo == _hi:
                _ts_to_dfs_pos[_ts] = _ts_to_dfs_pos[_lo]
            else:
                _frac = (_ts - _lo) / (_hi - _lo)
                _ts_to_dfs_pos[_ts] = (
                    _ts_to_dfs_pos[_lo] + _frac * (_ts_to_dfs_pos[_hi] - _ts_to_dfs_pos[_lo])
                )
    else:
        _ts_to_dfs_pos = {_ts: float(_i) for _i, _ts in enumerate(_all_ts)}

    # branch_id: the real call_id of the branch's own child (see
    # _detect_delegation_forks — "Fork branch node id = children ID", not a
    # synthetic counter), stamped on the reset boundary itself. _reset_branch_id
    # (from _assign_dfs_positions) is keyed by the ts the reset actually landed
    # on — strictly after the delegating tool call's own marker, not at it.
    for _ts, _node in state_reg.items():
        _node.dfs_pos = _ts_to_dfs_pos.get(_ts, 0.0)
        _node.is_branch_reset = _ts in _reset_branch_id
        if _ts in _reset_branch_id:
            _node.branch_id = _reset_branch_id[_ts]

    for _lane in lanes:
        for _el in _lane.sequence:
            if isinstance(_el, TransNode):
                _el.dfs_pos_start = _ts_to_dfs_pos.get(_el.start_ts, 0.0)
                _el.dfs_pos_end   = _ts_to_dfs_pos.get(_el.end_ts, 0.0)
                if _el.start_ts in _reset_branch_id:
                    _el.branch_id = _reset_branch_id[_el.start_ts]

    # ── Assign letter-suffix labels to thinking sub-states (S2 → S2a, S2b …)
    if thinking_sub_tss:
        from collections import defaultdict as _defdict
        all_bkts_tmp = sorted(state_reg.keys())
        _state_num_tmp = {b: i + 1 for i, b in enumerate(all_bkts_tmp)}
        # Group sub-states by their preceding numbered state
        _pred_to_subs: dict[int, list[float]] = _defdict(list)
        for _ts in sorted(thinking_sub_tss):
            _preds = [b for b in all_bkts_tmp if b < _ts - _TS_TOL]
            if _preds:
                _pn = _state_num_tmp[max(_preds)]
                _pred_to_subs[_pn].append(_ts)
        for _pn, _subs in _pred_to_subs.items():
            for _idx, _ts in enumerate(sorted(_subs)):
                state_reg[_ts].label_override = f"S{_pn}{chr(ord('a') + _idx)}"

    # ── Reassign sequential IDs globally (DFS order, level as tiebreaker)
    _LEVEL_ORDER = {"session": 0, "mas": 1, "agent": 2, "call": 3, "thinking": 4}
    all_trans = [el for lane in lanes for el in lane.sequence
                 if isinstance(el, TransNode)]
    all_trans.sort(key=lambda t: (t.dfs_pos_start, _LEVEL_ORDER.get(t.level, 9)))
    for i, t in enumerate(all_trans):
        t.seq = i + 1

    # ── Structural sanity check ──────────────────────────────────────────
    validate_trajectory_dag(state_reg, lanes)

    return state_reg, lanes
