#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Call tree construction and lane sequence derivation."""

from collections import defaultdict
from typing import Optional

from mas.lab.plots.multilevel_trajectory.constants import (
    _LLM_FOLLOW_UP_TYPES,
    _TS_TOL,
    PROCESSING_TYPE_WAIT_STATE,
)
# Tiny real (wall-clock) duration reserved for a delegation tool call's own
# dispatch instant — shared between _align_record_boundaries (which gives the
# delegate's own execution a start strictly after its dispatching tool call,
# not simultaneous with it) and _reserve_marker_width (which gives the Calls
# lane marker itself the matching nonzero width). Both must agree exactly, or
# the Agents lane and Calls lane disagree about when the handoff happens.
_MARKER_DUR = 0.001


def _layout_parent_id(rec: dict, by_id: dict[str, dict]) -> Optional[str]:
    """Parent used for lane layout: LLM follow-ups sit beside the LLM, not inside it."""
    pid = rec.get("parent_call_id")
    parent = by_id.get(pid) if pid is not None else None
    if (
        parent is not None
        and parent.get("call_type") == "LLMCall"
        and rec.get("call_type") in _LLM_FOLLOW_UP_TYPES
        and parent.get("parent_call_id") is not None
    ):
        return parent["parent_call_id"]
    return pid


def _build_call_tree(
    records: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, Optional[str]]]:
    """Compute parent–child relationships between execution records.

    Uses ``parent_call_id`` exclusively for all record types — O(n), exact.
    This is possible because the runtime threads a real ``caller_call_id``
    through the delegation contract itself (``InvokeEngineIo.call_id`` ->
    ``execute_engine_tool`` -> ``DelegationContract`` -> ``RunTurnFn``,
    resolved once by the driver via ``ObservabilityOperator.call_id_for``
    right before the engine is invoked — see
    ``runtime/src/mas/runtime/driver/driver.py``), so ``execution_start``
    events for delegated sub-agents carry the correct parent link — the
    delegating tool call's own ``call_id`` — for every delegation depth,
    without any temporal containment heuristic.

    Returns
    -------
    children_of : {call_id: [child_record, …]} sorted by ``start_ts``
    parent_of   : {call_id: parent_call_id or None}
    """
    children_of: dict[str, list[dict]] = defaultdict(list)
    parent_of:   dict[str, Optional[str]] = {}

    by_id = {r["call_id"]: r for r in records}
    for rec in records:
        pid = _layout_parent_id(rec, by_id)
        parent_of[rec["call_id"]] = pid
        if pid is not None:
            children_of[pid].append(rec)

    for cid in children_of:
        children_of[cid].sort(key=lambda r: r["start_ts"])

    return dict(children_of), parent_of


# ---------------------------------------------------------------------------
# Stage 2b — Structural boundary alignment
# ---------------------------------------------------------------------------

def _align_record_boundaries(
    records: list[dict],
    children_of: dict[str, list[dict]],
) -> None:
    """Align start/end timestamps between parents and children using the call
    tree topology.  No timestamp thresholds — purely structural.

    Rules applied for each parent's sorted children list:

    1. **First child** → ``child.start_ts = parent.start_ts``
       The first work item begins the instant the parent begins; the delta
       between ``execution_start`` and ``llm_call_start`` is Python overhead.

    2. **Consecutive siblings** → ``child_i.end_ts = child_{i+1}.start_ts``
       Adjacent work items share exactly one boundary state; no gap or overlap.

    3. **Last child** → ``child.end_ts = parent.end_ts``
       The last work item finishes when the parent finishes.

    Modifies records in-place through the shared dicts in ``children_of``.

    Note on processing gaps: when ``ProcessingCall`` records exist in the
    call tree (e.g. per-actor context injection spans), they participate in
    Rules 1–3 and naturally occupy the gap between the agent boundary and the first
    substantive call.  When no such record exists the gap is simply absorbed
    by Rule 1/3 — the absence is detectable by the caller via
    ``_make_call_sequence`` returning no ProcessingCall at fragment edges.
    """
    rec_by_id = {r["call_id"]: r for r in records}

    def _is_wait_state(rec: dict) -> bool:
        return (
            rec.get("call_type") == "ProcessingCall"
            and str(rec.get("processing_type") or "").strip().lower() == PROCESSING_TYPE_WAIT_STATE
        )

    # Envelope pass (bottom-up): a parent must temporally contain its children.
    # Multi-agent traces can close an outer scope before an inner one finishes —
    # e.g. mas_call_end fires as the last delegated peer returns, before the
    # entry agent's post-delegation synthesis LLM.  Without this, Rule 3 below
    # would snap the entry agent's end down to the premature MAS end and delete
    # its "tail" fragment (the moderator resuming after a delegation).  Extend
    # the parent to cover its children; never shrink a child to a stale parent.
    def _envelope(cid: str) -> tuple[float, float]:
        rec = rec_by_id.get(cid)
        kids = children_of.get(cid, [])
        if rec is None:
            return (float("inf"), float("-inf"))
        lo, hi = rec["start_ts"], rec["end_ts"]
        for k in kids:
            k_lo, k_hi = _envelope(k["call_id"])
            lo = min(lo, k_lo)
            hi = max(hi, k_hi)
        rec["start_ts"], rec["end_ts"] = lo, hi
        return (lo, hi)

    _child_ids = {k["call_id"] for kids in children_of.values() for k in kids}
    for r in records:
        if r["call_id"] not in _child_ids:  # roots
            _envelope(r["call_id"])

    for pid, kids in children_of.items():
        parent = rec_by_id.get(pid)
        if parent is None or not kids:
            continue
        sk = sorted(kids, key=lambda r: r["start_ts"])
        # Governance-orphaned LLM calls (no llm_call_end) have synthetic
        # boundaries; applying Rules 1 & 3 to their children (e.g. ToolCalls
        # for memory_search) would corrupt the children's real event
        # timestamps.  Skip both rules in that case so children keep their
        # accurate start/end from the emitted events.
        _orphan_llm_parent = (
            parent.get("_end_missing") and parent.get("call_type") == "LLMCall"
        )
        # Any parent whose end_ts is synthetic (no *_end event was emitted,
        # so it was set to start + 1.0 s or t_final as a structural fallback)
        # must not have Rule 3 applied: snapping the last child to an inflated
        # synthetic boundary corrupts the child's real event timestamp.
        # Classic case: the entry-agent AgentCall never receives
        # an execution_end in multi-agent delegation traces; its end_ts is
        # extended to t_final, and without this guard the last ToolCall
        # (the delegation tool) would be stretched across the entire trace.
        _orphan_parent = parent.get("_end_missing", False)
        # Rule 1 — snap the first work item (including ProcessingCall) to the
        # parent boundary.  The delta between execution_start and the first
        # child is pure Python/instrumentation overhead and must not appear
        # as a visible gap column.
        if not _orphan_llm_parent:
            sk[0]["start_ts"] = parent["start_ts"]
        # Exception: a delegation edge (parent is a ToolCall with an AgentCall
        # among its children — see _is_delegation_tool in _make_call_sequence,
        # which uses this exact "any child is agent-level" test, not just
        # sk[0], so this check must match it or the two can disagree about
        # whether a given ToolCall is a delegation edge at all). Snapping the
        # delegate's own execution to start in the identical instant as its
        # dispatching tool call leaves that tool call with no boundary of its
        # own: the Agents lane would show the delegate's box starting at the
        # exact same timestamp the Calls lane's delegate marker occupies, so
        # the marker visually reads as belonging to the delegate instead of
        # closing out the delegator (the bug this fixes). Give the tool a
        # tiny (1ms) real dispatch width instead — the delegate's own
        # execution starts just after, not simultaneously — using the same
        # _MARKER_DUR the Calls lane marker itself gets in
        # _reserve_marker_width, so both lanes agree on the exact same
        # handoff timestamp. `max()` rather than a direct assignment: if the
        # AgentCall isn't sk[0] (some other child genuinely starts earlier —
        # an anomalous but possible shape), its own start may already be
        # later than the epsilon floor, and must never be moved backward.
        if parent.get("call_type") == "ToolCall":
            _agent_child = next((k for k in sk if k.get("level") == "agent"), None)
            if _agent_child is not None:
                _agent_child["start_ts"] = max(
                    _agent_child["start_ts"], parent["start_ts"] + _MARKER_DUR
                )
        # Rule 3 — last child shares the parent's end boundary.
        # Skip for any parent with a synthetic end_ts (_end_missing).
        if not _orphan_parent and not _is_wait_state(sk[-1]):
            sk[-1]["end_ts"] = parent["end_ts"]
        # Rule 2 (consecutive siblings share boundaries — sequential only).
        # Skip when two children start at the same timestamp (parallel
        # execution, e.g. a moderator dispatching 3 agents concurrently):
        # naive closure would shrink the earlier record to zero-duration,
        # hiding its call-lane children entirely.
        for i in range(len(sk) - 1):
            # WAIT/RESUME are explicit boundary markers emitted by native
            # observability and already carry their own tiny duration. Stretching
            # them to the next sibling start corrupts call-lane ordering
            # (W1 no longer followed by the expected next state) and creates
            # visual gaps/misplaced states.
            if _is_wait_state(sk[i]):
                continue
            if sk[i + 1]["start_ts"] > sk[i]["start_ts"] + _TS_TOL:
                sk[i]["end_ts"] = sk[i + 1]["start_ts"]


# ---------------------------------------------------------------------------
# Stage 3a — Agent lane sequence: DFS on agent subtree
# ---------------------------------------------------------------------------

def _make_agent_sequence(
    records:     list[dict],
    children_of: dict[str, list[dict]],
    parent_of:   dict[str, Optional[str]],
) -> list[dict]:
    """Return the ordered list of agent-level records for the Agent lane.

    Delegation (``moderator`` spawning ``scheduler``) is resolved by DFS:
    the parent record is split into *pre-child* fragments (output cleared,
    parent still running) and a *tail* fragment (parent has finished,
    output is known).  The children are interleaved between the fragments.

    Example::

        moderator(0→10) delegates scheduler(4→8) →

        moderator_pre (0→4, output="")   ← parent running, no output yet
        scheduler     (4→8)
        moderator_tail(8→10)             ← parent done, output available

    No heuristics — boundaries come exclusively from the call tree.
    """
    rec_by_id = {r["call_id"]: r for r in records}

    def _has_agent_ancestor(rec: dict) -> bool:
        pid = parent_of.get(rec["call_id"])
        while pid is not None:
            if rec_by_id.get(pid, {}).get("level") == "agent":
                return True
            pid = parent_of.get(pid)
        return False

    agent_roots = sorted(
        [r for r in records if r["level"] == "agent" and not _has_agent_ancestor(r)],
        key=lambda r: r["start_ts"],
    )

    result: list[dict] = []

    def _agent_descendants(rec: dict) -> list[dict]:
        """Agent executions nested under *rec*, descending through non-agent
        calls (e.g. the delegation ToolCall that spawned a peer turn) but never
        past a nested agent — those belong to that agent's own subtree."""
        found: list[dict] = []
        for c in children_of.get(rec["call_id"], []):
            if c["level"] == "agent":
                found.append(c)
            else:
                found.extend(_agent_descendants(c))
        return found

    def dfs(rec: dict) -> None:
        agent_children = sorted(
            _agent_descendants(rec),
            key=lambda c: c["start_ts"],
        )
        if not agent_children:
            result.append(rec)
            return

        # Merge overlapping ranges of child agents.
        merged: list[tuple[float, float]] = []
        for ac in agent_children:
            if merged and ac["start_ts"] <= merged[-1][1] + _TS_TOL:
                merged[-1] = (merged[-1][0], max(merged[-1][1], ac["end_ts"]))
            else:
                merged.append((ac["start_ts"], ac["end_ts"]))

        pos = rec["start_ts"]
        for (cs, ce) in merged:
            if cs - pos > _TS_TOL:
                result.append({**rec, "start_ts": pos, "end_ts": cs,
                                "output": "", "_fragment": True})
            for ac in agent_children:
                if ac["start_ts"] >= cs - _TS_TOL and ac["end_ts"] <= ce + _TS_TOL:
                    dfs(ac)
            pos = ce

        if rec["end_ts"] - pos > _TS_TOL:
            result.append({**rec, "start_ts": pos, "end_ts": rec["end_ts"]})

    for root in agent_roots:
        dfs(root)

    # Do NOT sort by start_ts. dfs()'s recursion already appends fragments in
    # DFS order — the same reasoning as _make_call_sequence's own "do not
    # sort" comment applies here verbatim: a delegating agent's 2nd..Nth
    # sibling (a genuine fork branch, or a never-closed execution extended to
    # _t_final) keeps its own early dispatch timestamp even though its
    # subtree isn't explored — and doesn't advance real time — until DFS
    # reaches it, often long after an earlier sibling's own subtree has
    # already run for real seconds (or has been stretched to the trace's very
    # end). A chronological sort here re-mixes those fragments back into
    # wall-clock order, undoing the DFS order this function just built and
    # producing an Agents lane whose array order no longer matches the DFS
    # virtual-position axis every other lane is built from — the state
    # bordering one agent's fragment can end up positioned as if it belonged
    # to a completely different, unrelated branch.
    return result


# ---------------------------------------------------------------------------
# Stage 3b — Call lane: direct call children of each agent fragment
# ---------------------------------------------------------------------------

def _make_call_sequence(
    agent_sequence: list[dict],
    children_of:    dict[str, list[dict]],
    reset_branch_agent_call_ids: "frozenset[str] | set[str]" = frozenset(),
) -> list[dict]:
    """Return the ordered list of call-level records for the Call lane.

    For each agent record fragment we collect the *direct* call-level children
    (LLMCall, ToolCall, MemoryCall, RAGQuery, ProcessingCall) via the
    ``parent_call_id``-based call tree.

    **Delegation tool as a dispatch marker** (structural, zero name heuristics):

    A ToolCall is a *delegation tool* when its children in the call tree
    include at least one AgentCall record (``children_of[tool_id]`` contains
    a record with ``level == "agent"``) — this is the call that hands off to
    another agent, so this lane is a genuine DFS walk of the call tree: the
    delegation tool is the last node of the delegating agent's own segment,
    immediately followed by the delegate's own calls, exactly as they appear
    in the tree. Its *true* span covers the whole delegated execution (it's a
    synchronous call-and-wait, so its end_ts is whenever the delegate
    returns) — rendering it at full width would overlap the delegate's own
    calls, which occupy that same window in this shared lane row. It is
    rendered as an instant dispatch marker instead (a zero-width copy, timing
    only — the real record elsewhere is untouched), the same treatment any
    nearly-instant ToolCall already gets.

    ``reset_branch_agent_call_ids`` (agent call_ids that are the 2nd..Nth
    sibling of a fork — see ``_reset_branch_agent_call_ids``) each get their
    own subtree's first call-level record tagged ``_branch_entry``, and the
    delegating marker right before them gets a wider reserved gap than an
    ordinary marker (see ``_reserve_marker_width``). Both matter for
    ``_assign_dfs_positions``: an ordinary marker's end and the next record's
    start are deliberately made to *coincide* (one shared boundary, no gap —
    the normal case everywhere else in this lane); but for a reset-triggering
    marker, that would collapse the marker's own close and the new branch's
    entry onto the same timestamp, leaving nowhere to insert the
    branch-reset boundary and making the marker look like it belongs to the
    new branch instead of closing out the old one.
    """
    def _is_delegation_tool(rec: dict) -> bool:
        if rec["call_type"] != "ToolCall":
            return False
        # A delegation tool wraps a sub-agent execution: its children in the
        # call tree include at least one AgentCall record.
        return any(c["level"] == "agent" for c in children_of.get(rec["call_id"], []))

    result:   list[dict] = []
    seen_ids: set[str]   = set()

    def _collect(
        call_id: str,
        ts_start: float,
        ts_end: float,
        *,
        _ancestors: frozenset[str] = frozenset(),
        hard_upper: "float | None" = None,
    ) -> None:
        """Collect direct call-level children of ``call_id`` that fall within
        the fragment window ``[ts_start, ts_end]``.

        The window filter is needed when the same agent execution is split into
        multiple fragments by ``_make_agent_sequence`` (DFS interleaving):
        all fragments share the same ``call_id``, but each fragment must only
        claim calls that start within its time slice.

        NOTE on why this one still needs a time window at all (unlike the
        heuristics removed elsewhere this session): a repeatedly-delegated
        agent's OWN AgentCall record gets a distinct id per invocation
        (records.py's collision-renaming pass), but every LLMCall/ToolCall
        that invocation makes still carries the runtime's ORIGINAL (reused,
        coarser ``{agent_id}-{turn_id}-exec``) id as its own parent_call_id —
        the runtime itself doesn't hand out a distinct id per invocation for
        children to point at. So ``children_of[original_id]`` genuinely pools
        every invocation's children under one id; WHICH invocation a pooled
        child belongs to is not recoverable from any id in the trace at all,
        only from which invocation's time window it falls in. This is a real
        runtime-side gap (the fix would be threading a per-invocation id
        through the same way ``caller_call_id`` now is for delegation), not a
        lab-side choice to ignore an id that's already there.

        ``hard_upper``, when given, caps the upper bound below the usual
        ``ts_end + _TS_TOL`` slack — needed when this same ``call_id`` is
        shared by a LATER agent_sequence fragment too (a repeatedly-delegated
        agent reusing one runtime id): without this cap, an earlier turn's
        ``_TS_TOL`` tolerance can reach across the exact (Rule-2-set) zero-gap
        boundary and greedily claim (via ``seen_ids``, first-come-first-served)
        a call that starts just after the boundary but structurally belongs to
        the NEXT turn — leaving that later turn's own subtree looking empty.
        """
        if call_id in _ancestors:
            return
        next_ancestors = _ancestors | {call_id}
        upper = ts_end + _TS_TOL

        def _process_ordinary_child(child: dict) -> None:
            """Append `child` (and recurse into its own descendants) exactly
            as an ordinary call-level record — the generic per-child
            handling every call-tree child gets below, factored out so the
            native parallel_group branch-member handling (whose members are
            TRUE SIBLINGS of the group's own merged record in the call tree
            — verified against a real trace: their own ``parent_call_id`` is
            the delegating agent's call_id, not the parallel_group record's
            own call_id — never its call-tree children) can apply the exact
            same treatment to a member out of its natural list order.
            """
            # Delegation tool: last node of this agent's segment, rendered as
            # an instant dispatch marker (see docstring) — never recurse into
            # it (its only child is the AgentCall, filtered by the level
            # check above; the delegate's own calls are reached separately,
            # via agent_sequence's own entry for that agent).
            if _is_delegation_tool(child):
                marker = dict(child)
                marker["end_ts"] = marker["start_ts"]
                marker["_delegation_marker"] = True
                result.append(marker)
                seen_ids.add(child["call_id"])
                return
            # Governance-orphaned LLM calls are implementation artifacts:
            # governance intercepted the call before it completed and no
            # llm_call_end was emitted.  Showing them would produce two
            # consecutive LLM-call bars with no state node between them,
            # which is semantically invalid.  We still recurse so that any
            # ToolCall / non-LLM descendants (e.g. memory_search whose
            # parent_call_id points to the orphaned call) are promoted to
            # the call lane.
            is_orphan_llm = (
                child.get("_end_missing") and child["call_type"] == "LLMCall"
            )
            if not is_orphan_llm:
                result.append(child)
                seen_ids.add(child["call_id"])
            # Recurse for calls nested under non-LLM parents (e.g. ToolCall
            # wrapping sub-calls).  LLMCalls are leaves in new traces; but in
            # older traces a governance short-circuit leaves a stale LLM
            # call_id on the stack causing the next step's calls to be parented
            # under it — recurse in that case too so those calls are visible.
            if child["call_type"] != "LLMCall" or children_of.get(child["call_id"]):
                _collect(
                    child["call_id"],
                    child["start_ts"],
                    child["end_ts"],
                    _ancestors=next_ancestors,
                    hard_upper=hard_upper,
                )

        if hard_upper is not None:
            upper = min(upper, hard_upper)
        for child in children_of.get(call_id, []):
            if child["level"] != "call":
                continue
            if child["start_ts"] < ts_start - _TS_TOL:
                continue
            if child["start_ts"] > upper:
                continue
            if child["call_id"] in seen_ids:
                continue
            # Native parallel_group ("parallel fork"/"parallel aggregation",
            # merged into ONE call record by _build_call_records: start_ts=
            # dispatch instant, end_ts=aggregation-complete instant — the
            # runtime never emits an independent "parallel aggregation"
            # record of its own, see dag.py's own docstring on this same
            # fact). Appending it here as ONE atomic record (like any
            # ordinary call) would make dag.py's per-crec loop build its own
            # fork state AND its own join/merge state back-to-back, with
            # this group's own branch members rendered AFTER both — i.e.
            # "merge before the branches" (reported regression). The
            # members are NOT this record's own call-tree children (their
            # own ``parent_call_id`` is the SAME delegating agent as this
            # parallel_group record's own parent — verified against a real
            # trace: ``children_of[this parallel_group's call_id]`` is
            # empty), they're its SIBLINGS in ``children_of[call_id]`` (this
            # very list being iterated) — named by call_id in this record's
            # own ``tools`` list. Emit a zero-duration fork marker, then
            # process each member (found by call_id in this same sibling
            # list, sorted by dispatch order) via the exact same generic
            # per-child handling any ordinary sibling gets — marking each
            # one ``seen_ids`` so this same for-loop's own later, natural
            # iteration skips it (see the ``seen_ids`` guard just above) —
            # then a zero-duration join marker. `_pg_fork_marker`/
            # `_pg_join_marker` let dag.py tell the two apart — both still
            # carry the SAME call_id and processing_name ("parallel fork")
            # copied from the one merged record.
            if (
                child["call_type"] == "ProcessingCall"
                and str(child.get("processing_type") or "") == "parallel_group"
            ):
                fork_marker = dict(child)
                fork_marker["end_ts"] = fork_marker["start_ts"]
                fork_marker["_pg_fork_marker"] = True
                # Rule 2 (no-gap sibling boundaries) routinely leaves this
                # marker's own start_ts EXACTLY equal to the immediately
                # preceding record's own end_ts (e.g. the LLM call that
                # decided to fork ends the very instant the fork
                # dispatches). Deliberately left un-nudged: dag.py's
                # `_bridge_to_state` dedups by exact ts and, on a collision,
                # reuses that SAME already-registered StateNode (tagging it
                # is_fork=True in place) instead of minting a new, almost-
                # empty box next to it purely to carry the role tag —
                # avoids both the redundant box and the stale-looking
                # fork<->join cross-link this used to cause (reported: "why
                # do we have S16 AND S17?" / "merge hyperlink...points to
                # S16").
                result.append(fork_marker)
                seen_ids.add(child["call_id"])
                _member_ids = {
                    str(_t.get("call_id") or "") for _t in (child.get("tools") or [])
                }
                _members = sorted(
                    (
                        _sib for _sib in children_of.get(call_id, [])
                        if _sib["level"] == "call"
                        and _sib["call_id"] in _member_ids
                        and _sib["call_id"] not in seen_ids
                        and ts_start - _TS_TOL <= _sib["start_ts"] <= upper
                    ),
                    key=lambda _sib: _sib["start_ts"],
                )
                for _member in _members:
                    _process_ordinary_child(_member)
                join_marker = dict(child)
                join_marker["start_ts"] = join_marker["end_ts"]
                join_marker["_pg_join_marker"] = True
                # Same no-nudge reasoning as the fork half above — a
                # collision with the last branch member's own completion is
                # handled by dag.py's `_bridge_to_state` reuse, not here.
                result.append(join_marker)
                continue
            _process_ordinary_child(child)


    _tagged_branch_entries: set[str] = set()
    for _idx, arec in enumerate(agent_sequence):
        _mark = len(result)
        # A repeatedly-delegated agent's every invocation shares one runtime
        # call_id "pool" (children_of[pool_id]) — the FIRST invocation keeps
        # that id as its own call_id; every later invocation gets a suffixed
        # id from records.py but still queries the same pool via
        # _reused_call_id (see below). Either way, if a LATER agent_sequence
        # fragment shares this same pool, cap this fragment's own lookup(s)
        # at that later fragment's start — otherwise _collect's _TS_TOL
        # slack can reach across the exact (Rule-2-set, zero-gap) boundary
        # between them and steal a call that starts just after it but
        # structurally belongs to that later turn, leaving the later turn
        # looking like a genuinely empty invocation.
        _pool_id = arec.get("_reused_call_id") or arec["call_id"]
        _next_bound = next(
            (
                _later["start_ts"]
                for _later in agent_sequence[_idx + 1:]
                if (_later.get("_reused_call_id") or _later["call_id"]) == _pool_id
            ),
            None,
        )
        _collect(arec["call_id"], arec["start_ts"], arec["end_ts"], hard_upper=_next_bound)
        # records.py suffixes the call_id of the 2nd..Nth invocation of an
        # agent that reuses the same runtime call_id (e.g. schedule_agent
        # delegated to 3+ times), but every LLMCall/ToolCall that invocation
        # makes still carries the *original* (reused) id as its own
        # parent_call_id on the wire — the runtime has no notion of the
        # suffix. Those real children only exist in children_of[_reused_call_id]
        # (the pool shared by every invocation), never in
        # children_of[arec["call_id"]] — so without this, every invocation
        # past the first collects zero call-level records regardless of how
        # wide its fragment window is. Re-check that shared pool, filtered to
        # this fragment's own window, same as the primary lookup above.
        _reused = arec.get("_reused_call_id")
        if _reused:
            _collect(_reused, arec["start_ts"], arec["end_ts"], hard_upper=_next_bound)
        _cid = arec["call_id"]
        # Tag the first call-level record of a reset-triggering agent's own
        # subtree as its branch entry — only on the FIRST agent_sequence
        # fragment sharing this call_id (a delegate that itself further
        # delegates gets split into pre/tail fragments; only the pre-fragment
        # is the true branch entry).
        if _cid in reset_branch_agent_call_ids and _cid not in _tagged_branch_entries:
            if len(result) > _mark:
                result[_mark] = {**result[_mark], "_branch_entry": _cid}
                _tagged_branch_entries.add(_cid)
            else:
                # No call-level record survived (the reused-id lookup above
                # found nothing either — a genuinely empty invocation, e.g.
                # cut off before its first LLM call). Fall back to a
                # placeholder in this fragment's own window so the branch
                # still gets a reset separator instead of silently rendering
                # as if it were part of the previous branch — but never
                # append the raw agent-level record: this list backs the
                # Calls lane, which only ever holds LLM/Tool/Memory/RAG/
                # Processing records, and call_type="AgentCall" here would
                # render as a wrong-type, agent-colored "Agent" bar (dag.py's
                # trans() has no override for it, and constants.py maps
                # AgentCall to the agent palette/label and excludes it from
                # ever being instant). Recast as an empty ProcessingCall —
                # the same lightweight, no-real-content type already used
                # for synthesized markers elsewhere in this lane.
                result.append({
                    **arec,
                    "call_type": "ProcessingCall",
                    "label": "(no calls)",
                    "input": "",
                    "output": "",
                    "processing_name": "",
                    "_branch_entry": _cid,
                })
                _tagged_branch_entries.add(_cid)
        # Tag every record this fragment just contributed (across every
        # append site above) with its owning agent_sequence index — purely
        # additive bookkeeping (no existing consumer reads or is affected by
        # it), preserved verbatim through _reserve_marker_width's and
        # _stagger_coinc_processing_calls' own dict-spread copies. Lets
        # dag.py reconcile this fragment's own start_ts/end_ts against
        # whatever the Calls lane actually ends up with post-stagger — see
        # _reconcile_agent_fragment_boundaries in dag.py for why that can
        # legitimately differ from arec's own (pre-stagger) boundary.
        for _ri in range(_mark, len(result)):
            result[_ri] = {**result[_ri], "_agent_fragment_idx": _idx}

    result = _reserve_marker_width(result)

    # Do NOT sort by start_ts. _collect()'s recursion already appends in
    # DFS order (children_of[cid] is pre-sorted by start_ts at construction, so
    # within any single subtree DFS order already equals chronological
    # order); the only thing a final chronological sort accomplishes is
    # UNDOING DFS order specifically for sibling batches — a delegating
    # agent's 2nd..Nth sibling call (see _detect_delegation_forks) keeps its
    # own early dispatch timestamp even though its subtree isn't explored
    # (and doesn't advance real time) until DFS reaches it, often well after
    # an earlier sibling's own subtree has already run for real seconds. A
    # global chronological sort would put that later sibling BEFORE the
    # earlier sibling's own conclusion — a real backward jump in the
    # sequence that produced disconnected-looking gaps and out-of-order
    # call numbers. dag.py's DFS virtual-position axis (_assign_dfs_positions)
    # is what actually turns this DFS-ordered list into x-axis positions.
    return result


def _reserve_marker_width(seq: list[dict]) -> list[dict]:
    """Give each zero-duration delegation dispatch marker its own tiny slot,
    and give each branch entry a genuinely separate timestamp from whatever
    marker(s) precede it.

    _align_record_boundaries snaps a delegation marker's start_ts to exactly
    match the delegating agent's own dispatch instant — which, by the same
    Rule 1 ("first child starts when its parent starts"), is *also* the
    start_ts the delegate's own first call gets once it's appended later in
    this same sequence (via agent_sequence's own entry for that agent).
    Without a reserved width, the marker and the delegate's own first
    boundary collapse onto the identical pixel column (state_reg dedups by
    exact ts).

    Walks the whole (already DFS-ordered) sequence once with a monotonic
    "claimed up to" cursor — a plain pairwise ``seq[i+1]`` check isn't enough
    here: when an LLM turn dispatches several delegate_to_X calls at once
    (a sibling batch), ALL of their markers land consecutively in this list,
    collected together before ANY of their subtrees are visited (see
    ``_make_call_sequence``'s outer loop) — so the record immediately after
    a given reset-triggering marker is often the *next marker*, not that
    marker's own branch entry. Only ever advances timestamps forward, never
    backward — same rule as ``_stagger_coinc_processing_calls`` — and shifts
    a record's end_ts by the same delta as its start_ts when bumped, so a
    real (non-zero) duration is never corrupted.

    A record tagged ``_branch_entry`` (see ``_make_call_sequence``) needs a
    full extra tick beyond the cursor, not just snapped to meet it — a
    genuinely distinct timestamp from whatever marker(s) just closed out the
    delegating agent's own segment. Without that extra separation,
    ``_assign_dfs_positions`` would find the branch entry's start_ts already
    claimed (by the last marker's own end bookkeeping) and skip inserting
    the reset there entirely — the marker would then render as if it
    belonged to the new branch instead of closing out the old one.

    The cursor only ever ADVANCES on a ``_delegation_marker`` or
    ``_branch_entry`` record's own end — never on an ordinary call. An
    ordinary call is still checked against the cursor (so a fork's rank-0
    branch, whose first real call would otherwise collapse onto its own
    dispatching marker's timestamp, still gets pushed clear of the whole
    marker cluster), but it must never let the cursor advance PAST its own
    end: an ordinary ``_end_missing`` call's end_ts is a synthetic
    ``start_ts + 1.0`` placeholder, not a real duration — if it were allowed
    to raise the cursor, every subsequent real call in that same agent's own
    subtree would get dragged forward by however long that placeholder
    happens to be, corrupting a perfectly legitimate later call's real
    start/end (observed: a real 6s LLM call shifted a full extra second,
    stretching its bar past the point its own agent had already finished and
    the next agent had resumed — a call rendered as if straddling two
    different agents' segments).
    """
    cursor: float | None = None
    for i, rec in enumerate(seq):
        _is_special = bool(rec.get("_delegation_marker") or rec.get("_branch_entry"))
        min_start = rec["start_ts"] if cursor is None else cursor
        if rec.get("_branch_entry") and cursor is not None:
            min_start = cursor + _MARKER_DUR
        if rec["start_ts"] < min_start:
            delta = min_start - rec["start_ts"]
            orig_end = rec["end_ts"]
            rec = seq[i] = {
                **rec,
                "start_ts": rec["start_ts"] + delta,
                "end_ts": rec["end_ts"] + delta,
            }
            # This record's own boundary just moved forward by `delta`, but
            # the cursor above never advances on an ordinary (non-special)
            # record (see docstring). If the NEXT record(s) were touching
            # this one's *original* end_ts exactly — the "no-gap" sibling
            # boundary `_align_record_boundaries` (Rule 2) already set up —
            # leaving them at their old start_ts would strand that shared
            # boundary behind this record's new, later end: the two
            # timestamps that used to be the same instant would now differ,
            # surfacing as a phantom gap (or reversed ordering) in the
            # rendered lane. Cascade the same forward shift through every
            # consecutive record still exactly touching the previous one's
            # (pre-shift) end, restoring the shared boundary invariant.
            boundary = orig_end
            j = i + 1
            while j < len(seq) and abs(seq[j]["start_ts"] - boundary) <= 1e-6:
                nxt = seq[j]
                boundary = nxt["end_ts"]
                seq[j] = {**nxt, "start_ts": nxt["start_ts"] + delta, "end_ts": nxt["end_ts"] + delta}
                j += 1
        if rec.get("_delegation_marker") and abs(rec["end_ts"] - rec["start_ts"]) <= _TS_TOL:
            rec = seq[i] = {**rec, "end_ts": rec["start_ts"] + _MARKER_DUR}
        if _is_special:
            cursor = rec["end_ts"] if cursor is None else max(cursor, rec["end_ts"])
    return seq


# ---------------------------------------------------------------------------
# Stage 3c — Fork/Branch detection and the DFS virtual-position axis
# ---------------------------------------------------------------------------

def _native_parallel_group_by_tool_call_id(records: list[dict]) -> dict[str, str]:
    """Map each delegation ToolCall's own call_id to the native
    parallel-group id it was genuinely dispatched as part of.

    The runtime emits a real ``ProcessingCall(processing_type="parallel_group")``
    pair (fork + aggregation) exactly when an agent's own LLM turn decided
    ``next_step == "PARALLEL_TOOL_CALLS"`` — i.e. two or more tool calls (which
    may or may not themselves be delegations) genuinely dispatched together
    in one native fork. That record's own ``tools`` list enumerates the exact
    call_id of every tool call launched together in it — the only
    non-heuristic, native ground truth for "these calls are one real fork",
    as opposed to two delegate calls that merely happen to share the same
    delegating parent but were actually dispatched as separate, sequential
    decisions (see ``_detect_delegation_forks`` below).

    A ToolCall absent from this map was dispatched entirely on its own — no
    ``parallel_group`` ever wrapped it.
    """
    out: dict[str, str] = {}
    for rec in records:
        if rec.get("call_type") != "ProcessingCall":
            continue
        if str(rec.get("processing_type") or "") != "parallel_group":
            continue
        gid = str(rec.get("group_id") or "")
        if not gid:
            continue
        for tool in rec.get("tools") or []:
            tool_call_id = tool.get("call_id")
            if tool_call_id:
                out[str(tool_call_id)] = gid
    return out


def _detect_delegation_forks(
    records: list[dict],
    parent_of: dict[str, Optional[str]],
    rec_by_id: dict[str, dict],
) -> dict[str, list[dict]]:
    """Group delegate AgentCall records by their fork parent's own call_id.

    A "fork" is a call_id whose call tree has 2+ agent-level descendants
    reached through a delegation tool (same structural test as
    ``_is_delegation_tool`` above): the delegating agent issued 2+ delegate
    calls, whether they overlap in wall-clock time or run one after another.
    Each such delegate call is a "branch" — going back up to the shared fork
    parent and descending into a new child, in DFS order.

    This is intentionally purely tree-structural (NOT gated on whether the
    runtime ever genuinely dispatched the members together in one native
    ``parallel_group`` — see ``_native_parallel_group_by_tool_call_id``): it
    powers the DFS branch-reset / call-sequence separator machinery
    (``_reset_branch_agent_call_ids``, ``_make_call_sequence``), which is
    needed whenever an agent's call-tree has multiple delegate siblings at
    all — concurrent or sequential, and even for repeated delegation to the
    very same agent (whose reused call_id needs its own separator between
    turns). Using this same structural grouping to also decide the VISUAL
    fork/join badge (parallel_group_id/parallel_size shown in the UI) would
    be wrong — see dag.py's ``_native_visual_fork_groups`` for that
    narrower, native-gated notion of "fork".

    Returns ``{fork_parent_call_id: [delegate_record, ...]}`` (sorted by
    ``start_ts``), size-1 groups omitted — a single delegate call is not a
    fork. Groups are keyed by the fork parent's own real call_id, not a
    synthetic counter, so a "branch node id" is always an addressable node
    in the actual call tree.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if rec["level"] != "agent":
            continue
        tool_rec = rec_by_id.get(parent_of.get(rec["call_id"]) or "")
        if tool_rec is None or tool_rec.get("call_type") != "ToolCall":
            continue
        fork_parent_id = parent_of.get(tool_rec["call_id"])
        if fork_parent_id is None:
            continue
        groups[fork_parent_id].append(rec)
    return {
        fork_parent_id: sorted(members, key=lambda r: r["start_ts"])
        for fork_parent_id, members in groups.items()
        if len(members) >= 2
    }


def _reset_branch_agent_call_ids(fork_groups: dict[str, list[dict]]) -> set[str]:
    """The agent call_ids that are the 2nd..Nth sibling of some fork group.

    The first branch doesn't need a reset — it flows directly from the fork.
    Passed to ``_make_call_sequence`` so it can tag the first call-level
    record of each such agent's own subtree as a branch entry.
    """
    return {
        member["call_id"]
        for members in fork_groups.values()
        for member in members[1:]
    }


# Unitless virtual-clock tick advanced at a branch reset, instead of the real
# (often negative, or huge) wall-clock gap between a fork's siblings.
_RESET_TICK = 1.0


def _assign_dfs_positions(
    call_sequence: list[dict],
) -> tuple[dict[float, float], dict[float, str]]:
    """Assign every distinct timestamp in ``call_sequence`` a monotonic
    virtual position, in DFS order.

    ``call_sequence`` must already be DFS-ordered (``_make_call_sequence``'s
    own append order, with the wall-clock sort removed) and already tagged
    (``_branch_entry`` on the first record of each reset-triggering branch,
    and a wider marker gap ahead of it — see ``_make_call_sequence`` and
    ``_reserve_marker_width``) — this function does not reorder or tag
    anything, it only measures distances between consecutive boundaries as
    it walks the list once.

    Within a branch, the cursor advances by the real elapsed time between
    consecutive boundaries, so relative call durations stay proportional —
    matching today's proportional/log width modes. At a record tagged
    ``_branch_entry`` (the first call of a fork's 2nd..Nth sibling), the
    cursor does not advance by the real gap (which can be negative: this
    sibling was dispatched before the previous one's subtree finished
    exploring) — it advances by a small fixed tick instead.

    Returns
    -------
    ts_to_pos: ``{ts: dfs_pos}`` — every boundary timestamp seen, in the
        virtual coordinate space that drives the x-axis.
    reset_branch_id: ``{ts: branch_call_id}`` — the subset of ts_to_pos's
        keys that are themselves a branch-reset boundary (renderer draws a
        full-height separator there), mapped to the branch's own call_id.
    """
    ts_to_pos: dict[float, float] = {}
    reset_branch_id: dict[float, str] = {}
    cursor = 0.0
    prev_end: float | None = None
    for rec in call_sequence:
        s, e = rec["start_ts"], rec["end_ts"]
        branch_entry = rec.get("_branch_entry")
        if s not in ts_to_pos:
            if prev_end is None:
                ts_to_pos[s] = cursor
            elif branch_entry:
                cursor += _RESET_TICK
                ts_to_pos[s] = cursor
                reset_branch_id[s] = branch_entry
            else:
                cursor += max(0.0, s - prev_end)
                ts_to_pos[s] = cursor
        if e not in ts_to_pos:
            cursor += max(0.0, e - s)
            ts_to_pos[e] = cursor
        prev_end = e
    return ts_to_pos, reset_branch_id
