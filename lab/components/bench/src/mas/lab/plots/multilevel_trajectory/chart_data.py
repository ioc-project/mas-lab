#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""D3 chart data serialization and HTML template formatting."""

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Union

from mas.lab.plots.multilevel_trajectory.constants import _EMPTY_GAP_MAX_S, _TS_TOL
from mas.lab.plots.multilevel_trajectory.dag import _build_dag
from mas.lab.plots.multilevel_trajectory.models import LaneDef, StateNode, TransNode
from mas.lab.plots.multilevel_trajectory.records import _build_call_records
from mas.lab.plots.multilevel_trajectory.svg import _esc
from mas.lab.plots.trajectory import load_trace


def _trans_time_pairs(
    lanes: list[LaneDef],
    keep: Callable[[TransNode], bool],
) -> set[tuple[float, float]]:
    return {
        (el.start_ts, el.end_ts)
        for lane in lanes
        for el in lane.sequence
        if isinstance(el, TransNode) and keep(el)
    }


def _build_chart_data(
    state_reg: dict[float, StateNode],
    lanes: list[LaneDef],
    title: str,
    width_mode: str,
    show_user_actors: bool = True,
    show_time_axis: bool = True,
    annotations: dict | None = None,
    records: list[dict] | None = None,
) -> dict:
    """Serialize the DAG to a JSON-serialisable dict for the D3 renderer.

    Boundaries are already structurally aligned by ``_align_record_boundaries``
    before the DAG is assembled, so every lane uses the exact same timestamp
    set — no heuristic timestamp merging needed here.
    """
    # DFS virtual-position order — NOT real timestamp order (see tree.py's
    # _assign_dfs_positions / dag.py's finalization pass). A delegating
    # agent's 2nd..Nth sibling call keeps its own early dispatch timestamp
    # even though its subtree isn't explored until DFS reaches it, often well
    # after an earlier sibling's own subtree has advanced real time past it —
    # sorting by raw ts would put that later sibling before the earlier
    # sibling's own conclusion. dfs_pos is what actually reflects DFS order.
    all_buckets = sorted(state_reg.keys(), key=lambda ts: state_reg[ts].dfs_pos)
    _numbered_buckets2 = [b for b in all_buckets if not state_reg[b].label_override]
    _numbered_seq2     = {b: i + 1 for i, b in enumerate(_numbered_buckets2)}
    # A label-overridden bucket (thinking sub-state, connector-only, …) has no
    # number of its own — it inherits whichever numbered bucket precedes it.
    # "Precedes" means in all_buckets' own order (DFS position), not by raw
    # ts value — those can now differ (see all_buckets' own comment above).
    state_num: dict[float, int] = {}
    _last_numbered = 1
    for _b in all_buckets:
        if _b in _numbered_seq2:
            _last_numbered = _numbered_seq2[_b]
        state_num[_b] = _last_numbered

    bucket_to_lanes: dict[float, list[int]] = defaultdict(list)
    for li, lane in enumerate(lanes):
        seen: set[float] = set()
        for el in lane.sequence:
            if isinstance(el, StateNode) and el.ts not in seen:
                bucket_to_lanes[el.ts].append(li)
                seen.add(el.ts)

    shared_buckets = [
        {"ts": b, "laneIndices": sorted(set(ls))}
        for b, ls in sorted(bucket_to_lanes.items())
        if len(set(ls)) >= 2
    ]

    # Parallel group blocks are anchored strictly on native telemetry:
    # processing_call(parallel_group) records carrying group_id and tools.
    _pg_catalog: dict[str, dict[str, Any]] = {}
    for _r in records or []:
        if str(_r.get("call_type") or "") != "ProcessingCall":
            continue
        if str(_r.get("processing_type") or "") != "parallel_group":
            continue
        _gid = str(_r.get("group_id") or "").strip()
        if not _gid:
            continue
        _tools = list(_r.get("tools") or [])
        _members: list[dict[str, Any]] = []
        for _t in _tools:
            if not isinstance(_t, dict):
                continue
            _members.append(
                {
                    "correlationId": _t.get("correlation_id"),
                    "toolName": str(_t.get("tool_name") or ""),
                    "toolCallId": str(_t.get("call_id") or ""),
                }
            )
        _entry = _pg_catalog.setdefault(
            _gid,
            {
                "groupId": _gid,
                "source": "native_processing_call",
                "processingCallId": str(_r.get("call_id") or ""),
                "forkTs": float(_r.get("start_ts") or 0.0),
                "joinTs": float(_r.get("end_ts") or 0.0),
                "size": int(_r.get("tool_count") or len(_members) or 0),
                "members": _members,
            },
        )
        _entry["forkTs"] = min(float(_entry.get("forkTs") or _r.get("start_ts") or 0.0), float(_r.get("start_ts") or 0.0))
        _entry["joinTs"] = max(float(_entry.get("joinTs") or _r.get("end_ts") or 0.0), float(_r.get("end_ts") or 0.0))
        _entry["size"] = max(int(_entry.get("size") or 0), int(_r.get("tool_count") or len(_members) or 0))
        if not _entry.get("processingCallId"):
            _entry["processingCallId"] = str(_r.get("call_id") or "")
        if _members and not _entry.get("members"):
            _entry["members"] = _members

    parallel_groups = sorted(_pg_catalog.values(), key=lambda d: (d.get("forkTs") or 0.0, d.get("groupId") or ""))

    ann = annotations or {}

    # gap_ts_set: timestamps where at least one pin was resolved.
    # ALL lanes sharing that timestamp get gapMarker=true (visible ring).
    # pin_map: (lane_id, ts) → {note, excerpt}  — only the matching agent lane
    # carries the annotation text; other lanes get the ring only.
    gap_ts_set: set[float] = set()
    pin_map: dict[tuple[str, float], dict] = {}
    pins = ann.get("pins") or []
    if pins and records:
        cid_to_rec = {r["call_id"]: r for r in records if r.get("call_id")}
        for pin in pins:
            cid = str(pin.get("call_id", "")).strip()
            if not cid:
                continue
            matched = cid_to_rec.get(cid)
            if matched is None:
                # prefix match
                for rec_id, rec in cid_to_rec.items():
                    if rec_id.startswith(cid) or cid.startswith(rec_id):
                        matched = rec
                        break
            if matched:
                end_ts = matched.get("end_ts") or matched.get("start_ts")
                if end_ts:
                    gap_ts_set.add(end_ts)   # ring on every lane at this ts
                    for lane in lanes:
                        if (lane.lane_id == "agents"
                                or lane.lane_id.lower() == matched.get("agent_id", "").lower()):
                            pin_map[(lane.lane_id, end_ts)] = {
                                "note":    str(pin.get("note", "") or ""),
                                "excerpt": str(pin.get("excerpt", "") or ""),
                            }
                            break

    lanes_json = []
    for lane in lanes:
        seq: list[dict] = []
        for el in lane.sequence:
            if isinstance(el, StateNode):
                _pin = pin_map.get((lane.lane_id, el.ts), {})
                seq.append({
                    "type":          "state",
                    "ts":            el.ts,
                    "num":           state_num.get(el.ts, 0),
                    "nodeId":        el.node_id,
                    "labelOverride": el.label_override,
                    "hover":         el.hover,
                    "hoverByLane":   el.hover_by_lane,
                    "agentId":       el.agent_id,
                    "userEntry":     el.is_user_entry,
                    "userExit":      el.is_user_exit,
                    "laneRestart":   el.is_lane_restart,
                    "connectorOnly": el.ts in lane.connector_only_ts or el.is_connector_only,
                    "interrupted":   el.is_interrupted,
                    "isError":       el.is_error,
                    "gapMarker":     el.ts in gap_ts_set,
                    "pinNote":       _pin.get("note")    or None,
                    "pinExcerpt":    _pin.get("excerpt") or None,
                    **({"isBranchBegin": True, "parallelGroupId": el.parallel_group_id,
                        "parallelSize": el.parallel_size,
                        **({"branchPartnerTs": el.branch_partner_ts} if el.branch_partner_ts is not None else {}),
                        } if el.is_branch_begin else {}),
                    **({"isBranchEnd": True, "parallelGroupId": el.parallel_group_id,
                        "parallelSize": el.parallel_size,
                        **({"branchPartnerTs": el.branch_partner_ts} if el.branch_partner_ts is not None else {}),
                        } if el.is_branch_end else {}),
                    **({"isFork": True, "parallelGroupId": el.parallel_group_id,
                        "parallelSize": el.parallel_size,
                        "forkBranchTs": el.fork_branch_ts,
                        **({"joinTs": el.join_ts} if el.join_ts is not None else {}),
                        **({"forkKind": el.fork_kind} if el.fork_kind else {}),
                        } if el.is_fork else {}),
                    **({"isJoin": True, "parallelGroupId": el.parallel_group_id,
                        "parallelSize": el.parallel_size,
                        "joinOf": el.join_of,
                        **({"joinForkTs": el.join_fork_ts} if el.join_fork_ts is not None else {}),
                        **({"assemblyNote": el.assembly_note} if el.assembly_note else {}),
                        **({"forkKind": el.fork_kind} if el.fork_kind else {}),
                        } if el.is_join else {}),
                    **({
                        "cprData": el.cpr_data,
                        "model":   el.model,
                        "cprMode": el.cpr_mode,
                        "contextOperation": el.context_operation,
                    } if el.cpr_data else {}),
                    **({
                        "waitLinkId": el.wait_link_id,
                        "waitRole": el.wait_role,
                        "waitNote": el.wait_note,
                        "waitMeta": el.wait_meta,
                    } if el.wait_link_id else {}),
                    **({
                        "governance": el.governance,
                        "governanceColor": el.governance_color,
                    } if el.governance else {}),
                })
            elif isinstance(el, TransNode):
                _td: dict[str, Any] = {
                    "type":     "trans",
                    "callType": el.call_type,
                    "label":    el.label,
                    "callId":   el.call_id,
                    "agentId":  el.agent_id,
                    "processingType": el.processing_type,
                    "processingName": el.processing_name,
                    "startTs":  el.start_ts,
                    "endTs":    el.end_ts,
                    "seq":      el.seq,
                    "color":    el.color,
                    "hoverIn":  el.hover_in,
                    "hoverOut": el.hover_out,
                    "isInstant": el.is_instant,
                }
                if el.connector_only:
                    _td["connectorOnly"] = True
                if el.missing_telemetry:
                    _td["missingTelemetry"] = list(el.missing_telemetry)
                if el.cpr_data:
                    _td["cprData"] = el.cpr_data
                    _td["cprMode"] = el.cpr_mode
                    if el.context_operation:
                        _td["contextOperation"] = el.context_operation
                if el.model:
                    _td["model"] = el.model
                if el.parallel_group_id:
                    _td["parallelGroupId"] = el.parallel_group_id
                    _td["parallelRank"]    = el.parallel_rank
                    _td["parallelSize"]    = el.parallel_size
                if el.governance:
                    _td["governance"] = el.governance
                    _td["governanceColor"] = el.governance_color
                if el.governance_egress:
                    _td["governanceEgress"] = el.governance_egress
                    _td["governanceEgressColor"] = el.governance_egress_color
                if el.governance_ingress:
                    _td["governanceIngress"] = el.governance_ingress
                    _td["governanceIngressColor"] = el.governance_ingress_color
                if el.retry_group_id:
                    _td["retryGroupId"] = el.retry_group_id
                    _td["retryAttempt"] = el.retry_attempt
                seq.append(_td)
        _lane_d: dict[str, Any] = {
            "laneId":   lane.lane_id,
            "level":    lane.level,
            "label":    lane.label,
            "sequence": seq,
        }
        if lane.parallel_spans:
            _lane_d["parallelSpans"] = lane.parallel_spans
        lanes_json.append(_lane_d)

    t_min_chart = all_buckets[0]  if all_buckets else 0.0
    t_max_chart = all_buckets[-1] if all_buckets else 0.0

    # Per-gap classification driving the JS x-axis (computeBaseXPos/applyZoom/
    # computeMinZoom): one entry per adjacent pair in all_buckets (already
    # DFS-ordered). "reset" gaps (a branch boundary — see dag.py's finalization
    # pass) and "instant" gaps (a near-zero-duration call, e.g. a delegation
    # dispatch marker) both get a small fixed column width, never
    # time-proportional and never scaled by zoom; everything else is "normal".
    # A ``connector_only`` transition (the wait/resume bridge — see dag.py's
    # trans()) renders no bar/label/badge at all, only the dotted lifeline —
    # unlike a real "instant" marker (e.g. the delegation dispatch's own
    # ToolCall icon+label), there is nothing to centre or keep clickable here,
    # so it gets its own, narrower "connector" column instead of reusing the
    # full INSTANT_COL_W — otherwise it reserves a visibly empty dashed gap
    # roughly as wide as a normal content column (e.g. between a WAIT state
    # and the delegate's own first state right after it).
    _connector_pairs = _trans_time_pairs(lanes, lambda el: el.connector_only)
    _span_pairs = _trans_time_pairs(lanes, lambda el: not el.connector_only)
    _instant_pairs = _trans_time_pairs(
        lanes,
        lambda el: el.is_instant and el.call_type != "ProcessingCall",
    ) - _connector_pairs

    # Parallel tool-call batches can create DFS-adjacent bucket pairs whose
    # real timestamps go backward/near-equal (same parent agent launches
    # overlapping tools; DFS explores one subtree before returning to a
    # sibling's early boundary). Treat those as reset gaps to avoid collapsing
    # width columns from raw wall-clock deltas.
    _parallel_tool_groups: list[list[dict]] = []
    if records:
        _tools_by_parent: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for _r in records:
            if _r.get("call_type") != "ToolCall":
                continue
            _pid = _r.get("parent_call_id")
            if not _pid:
                continue
            _tools_by_parent[(_r.get("agent_id", ""), _pid)].append(_r)
        for _grp in _tools_by_parent.values():
            _grp_sorted = sorted(_grp, key=lambda r: (r["start_ts"], r["end_ts"]))
            _has_overlap = False
            for _i, _a in enumerate(_grp_sorted):
                for _b in _grp_sorted[_i + 1:]:
                    if _b["start_ts"] <= _a["end_ts"] + _TS_TOL and _a["start_ts"] <= _b["end_ts"] + _TS_TOL:
                        _has_overlap = True
                        break
                if _has_overlap:
                    break
            if _has_overlap:
                _parallel_tool_groups.append(_grp_sorted)

    # _TS_TOL (~50ms) is meant for "same real-world instant, different
    # clock/bookkeeping source" comparisons (e.g. WAIT/RESUME) — far too
    # coarse here: real native tool-call gaps within one parallel batch are
    # routinely sub-millisecond, so using _TS_TOL as the forward-progress
    # guard below flagged virtually every intra-branch boundary in a batch
    # as a "reset" (reported: "vertical yellow bars don't correspond to
    # anything meaningful" — they appeared at nearly every state instead of
    # only at genuine sibling-branch boundaries).
    _PARALLEL_RESET_HIT_TOL = 1e-3

    def _is_parallel_tool_reset(_prev_ts: float, _ts: float) -> bool:
        # A reset is specifically a BACKWARD (or exactly-tied) real-time
        # jump — DFS returning to a sibling whose own dispatch landed
        # chronologically before the previous sibling's subtree finished.
        # Any genuine forward progress, however small, is normal sequential
        # advance within the SAME branch and must never be flagged, so this
        # guard uses near-zero (float-noise-only) slack, not _TS_TOL.
        if _ts > _prev_ts + 1e-9:
            return False
        for _grp in _parallel_tool_groups:
            _prev_hits = [
                _r for _r in _grp
                if abs(_prev_ts - _r["start_ts"]) <= _PARALLEL_RESET_HIT_TOL or abs(_prev_ts - _r["end_ts"]) <= _PARALLEL_RESET_HIT_TOL
            ]
            if not _prev_hits:
                continue
            _ts_hits = [
                _r for _r in _grp
                if abs(_ts - _r["start_ts"]) <= _PARALLEL_RESET_HIT_TOL or abs(_ts - _r["end_ts"]) <= _PARALLEL_RESET_HIT_TOL
            ]
            if not _ts_hits:
                continue
            if any(_a.get("call_id") != _b.get("call_id") for _a in _prev_hits for _b in _ts_hits):
                return True
        return False

    bucket_gaps = []
    for _i in range(1, len(all_buckets)):
        _prev_ts, _ts = all_buckets[_i - 1], all_buckets[_i]
        _gap: dict[str, Any] = {}
        if state_reg[_ts].is_branch_reset:
            _gap["kind"] = "reset"
            # Scope: an agent-delegation branch reset (this DFS boundary
            # steps back up the call tree into a sibling AGENT) visually
            # concerns both the Agents and Calls lanes — the reset really
            # is "a different agent's own subtree starts here". A same-
            # agent tool fan-out reset (see _is_parallel_tool_reset below)
            # never leaves that agent's own row, so it only concerns the
            # Calls lane. Renderer (multilevel.html) uses this to scope the
            # branch-reset guide line's vertical extent instead of always
            # spanning every lane regardless of which kind of fork it is.
            _gap["scope"] = "agent"
            if state_reg[_ts].branch_id:
                _gap["branchId"] = state_reg[_ts].branch_id
        elif _is_parallel_tool_reset(_prev_ts, _ts):
            _gap["kind"] = "reset"
            _gap["scope"] = "tool"
        elif (_prev_ts, _ts) in _connector_pairs:
            _gap["kind"] = "connector"
        elif (_prev_ts, _ts) in _instant_pairs:
            _gap["kind"] = "instant"
        elif (_prev_ts, _ts) not in _span_pairs and (_ts - _prev_ts) < _EMPTY_GAP_MAX_S:
            # A sub-5 ms step that no transition spans (agent start overhead
            # before its first call) would otherwise get a full proportional
            # column — hundreds of empty pixels in front of the first bar.
            _gap["kind"] = "connector"
        else:
            _gap["kind"] = "normal"
        bucket_gaps.append(_gap)

    return {
        "title":            title,
        "widthMode":        width_mode,
        "buckets":          all_buckets,
        "bucketGaps":       bucket_gaps,
        "parallelGroups":   parallel_groups,
        "sharedBuckets":    shared_buckets,
        "lanes":            lanes_json,
        "showUserActors":   show_user_actors,
        "tMin":             t_min_chart,
        "tMax":             t_max_chart,
        "showTimeAxis":     show_time_axis,
        "highlightAgents":  ann.get("highlight_agents") or [],
    }


_D3_TEMPLATE = Path(__file__).parent.parent / "assets" / "multilevel.html"
_D3_VENDORED = Path(__file__).parent.parent / "assets" / "d3.min.js"


def _d3_inline_script() -> str:
    """Vendored d3 source, so the generated HTML renders offline (no CDN).

    Falls back to the CDN script tag if the vendored file is missing.
    """
    try:
        return _D3_VENDORED.read_text(encoding="utf-8")
    except OSError:
        return '</script><script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js">'


def _fmt_d3_html(data: dict) -> str:
    chart_json = json.dumps(data, ensure_ascii=False, default=str)
    tmpl = _D3_TEMPLATE.read_text(encoding="utf-8")
    # Replace {title}/{chart_json} first, then inject d3 last so its (minified)
    # body is never scanned for those placeholders.
    return (
        tmpl
        .replace("{title}", _esc(data["title"]))
        .replace("{chart_json}", chart_json)
        .replace("/*__MAS_D3_INLINE__*/", _d3_inline_script())
    )


def build_trajectory_chart_data(
    trace: "Union[str, Path, list[dict]]",
    title: str = "MAS Multilevel Trajectory",
    width_mode: str = "log",
    show_user_actors: bool = True,
    show_time_axis: bool = True,
    show_provenance: bool = True,
    annotations: dict | None = None,
    enabled_facets: "set[str] | None" = None,
) -> dict:
    """Build the chart data dict for the D3 multilevel renderer.

    ``enabled_facets`` toggles the optional annotation layers (cpr,
    governance, annotations, thinking — see dag.py's ``_ALL_FACETS``)
    overlaid on top of the core call-tree structure; ``None`` (default)
    enables all of them. ``show_provenance=False`` is sugar for excluding
    ``"cpr"`` alone — kept for backward compatibility.

    This is the **data-preparation stage** of the pipeline — equivalent to
    the server side when the UI path is used.  The browser renders the chart
    via the D3 renderer already loaded in the ``<iframe>`` by calling
    ``iframe.contentWindow.masUpdateChart(data)`` or via ``postMessage``.

    Returns
    -------
    dict
        JSON-serialisable dict that the ``multilevel.html`` D3 renderer
        expects as ``const CHART = {...}``.  Pass it to :func:`_fmt_d3_html`
        to get a self-contained HTML page (pipeline / static path), or send
        it over WebSocket as ``{type: "viz_data", data: <this dict>}`` for
        the live UI path.

    Examples
    --------
    Pipeline (static)::

        data = build_trajectory_chart_data(events, title="My Trace")
        html = _fmt_d3_html(data)   # → self-contained HTML artifact

    UI / WebSocket (live)::

        # Server
        data = build_trajectory_chart_data(events_list, title="My Trace")
        await ws.send_json({"type": "viz_data", "pane_id": "trace-traj", "data": data})

        # Browser (multilevel.html postMessage listener)
        iframe.contentWindow.postMessage({type: "mas_chart_update", data: data}, "*")
        # → masUpdateChart(data) re-renders the chart in-place
    """
    if not isinstance(trace, list):
        trace = load_trace(trace)
    if not trace:
        return {}
    records = _build_call_records(trace)
    if not records:
        return {}
    state_reg, lanes = _build_dag(
        records, trace, show_provenance=show_provenance, enabled_facets=enabled_facets
    )
    if not lanes:
        return {}
    return _build_chart_data(
        state_reg, lanes,
        title=title,
        width_mode=width_mode,
        show_user_actors=show_user_actors,
        show_time_axis=show_time_axis,
        annotations=annotations,
        records=records,
    )


def build_trajectory_chart_data_from_kg(
    source: "Any",
    query: "Any | None" = None,
    title: str = "MAS Multilevel Trajectory",
    width_mode: str = "log",
    show_user_actors: bool = True,
    show_time_axis: bool = True,
    show_provenance: bool = True,
    enabled_facets: "set[str] | None" = None,
) -> dict:
    """KG-backed variant of :func:`build_trajectory_chart_data`.

    Used by the WebSocket handler ``_handle_kg_viz`` to compute the chart
    data dict that is sent to the browser as ``{type: "viz_data", data: ...}``.
    The browser's ``masUpdateChart(data)`` re-renders the D3 chart in-place
    without reloading D3 from CDN.

    Parameters
    ----------
    source:
        :class:`~mas.lab.plots.kg_adapter.KGSource` instance, ``dict`` (raw
        KG), or ``str``/``Path`` (kg.json file path).
    query:
        Optional :class:`~mas.lab.plots.kg_adapter.FacetQuery`.
    """
    from mas.lab.plots.kg_adapter import KGSource, FacetQuery as _FQ

    if isinstance(source, (str, Path, dict)):
        src = KGSource(source) if isinstance(source, dict) else KGSource.from_file(source)
    else:
        src = source

    q = FacetQuery.from_dict(query) if isinstance(query, dict) else query  # type: ignore[assignment]
    records, events = src.load(q)
    if not records:
        return {}
    state_reg, lanes = _build_dag(
        records, events, show_provenance=show_provenance, enabled_facets=enabled_facets
    )
    if not lanes:
        return {}
    return _build_chart_data(
        state_reg, lanes,
        title=title,
        width_mode=width_mode,
        show_user_actors=show_user_actors,
        show_time_axis=show_time_axis,
        records=records,
    )
