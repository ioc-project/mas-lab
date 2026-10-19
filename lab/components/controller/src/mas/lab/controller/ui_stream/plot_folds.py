#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fold native events into plot artefacts when no pipeline stream step is running.

Chat / topology snapshots feed the mas-lab ``plot`` host. Series, diracs, and
story ticks come from optional UI folds (entry point ``mas.lab.ui.folds`` or
``MAS_UI_FOLDS=module:Class``). This module does not run detectors.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from typing import Any, Callable

from mas.lab.controller.ui_stream.refs import DataSourceRef
from mas.lab.controller.ui_stream.spec_snapshot import _HUB_HINTS, _HUB_TOOLS, pick_entry


_CHANNEL_SPLIT = re.compile(r"<\|?channel\|?>", re.I)
_DELEGATE_PREFIXES = ("delegate_to_", "delegate:", "handoff_to_")


def _visible_text(raw: str) -> str:
    """Drop Gemma/Harmony channel wrappers; keep the visible reply."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if "<|channel>" in text or "<channel|>" in text:
        parts = [p.strip() for p in _CHANNEL_SPLIT.split(text) if p.strip()]
        visible = [p for p in parts if p.lower() not in {"thought", "commentary", "analysis"}]
        if visible:
            text = visible[-1]
    return text.strip()


def _text(event: dict[str, Any]) -> str:
    raw = event.get("content") or event.get("output") or event.get("text") or event.get("input") or ""
    if isinstance(raw, dict):
        raw = raw.get("content") or raw.get("output") or raw.get("text") or raw.get("input") or ""
    return _visible_text(str(raw or ""))


def _delegate_peer(tool: str) -> str:
    name = str(tool or "").strip()
    for prefix in _DELEGATE_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :].strip()
    return ""


def _event_t(event: dict[str, Any]) -> float | None:
    raw = event.get("timestamp", event.get("ts", event.get("t")))
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value / 1000.0 if value > 1e12 else value
    if isinstance(raw, str) and raw.strip():
        try:
            value = float(raw)
        except ValueError:
            return None
        return value / 1000.0 if value > 1e12 else value
    return None


def _stamp_t0(state: dict[str, Any], t: float | None) -> None:
    if t is None or t == 0.0:
        return
    prev = state.get("t0")
    if prev is None:
        state["t0"] = float(t)
        return
    prev_f = float(prev)
    # Session / missing rows at epoch-0 must not pin t0 when the trace is unix time.
    if prev_f > 1e9 and t < 1e7:
        return
    if t > 1e9 and prev_f < 1e7:
        state["t0"] = float(t)
        return
    state["t0"] = min(prev_f, float(t))


def _args(event: dict[str, Any]) -> str:
    raw = event.get("arguments") or event.get("args")
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)[:4000]
    return str(raw).strip()[:4000]


def _is_human(event: dict[str, Any], agent: str) -> bool:
    """True only for the human operator, not an LLM chat ``role: user``."""
    who = str(event.get("agent_id") or agent)
    return who in {"user", "human"}


def _looks_like_briefing(text: str) -> bool:
    """Agent system/task markdown is not the opening user query."""
    head = (text or "").lstrip()
    if not head:
        return False
    if head.startswith("#") or head.startswith("**"):
        return True
    return "\n## " in head[:400]


# Same-wave parallel delegates share a timestamp; a later fork is a new turn.
_WAVE_GAP_S = 2.0


def _note_hub(state: dict[str, Any], event: dict[str, Any]) -> None:
    agent = str(event.get("agent_id") or "").strip()
    if agent.lower() in _HUB_HINTS:
        state["_hub"] = agent
    tool = str(event.get("tool_name") or "")
    if _delegate_peer(tool):
        state["_hub"] = agent
    elif tool in _HUB_TOOLS and agent:
        state.setdefault("_hub", agent)


def _retag_depths(state: dict[str, Any], cards: list[dict[str, Any]]) -> None:
    hub = str(state.get("_hub") or "")
    if not hub:
        for row in cards:
            who = str(row.get("from") or "")
            if who.lower() in _HUB_HINTS:
                hub = who
                state["_hub"] = hub
                break
    if not hub:
        return
    for row in cards:
        who = str(row.get("from") or "")
        if row.get("role") == "user" or who in {"user", "human"}:
            row["depth"] = 0
        elif who == hub:
            row["depth"] = 1
        elif who:
            row["depth"] = 2


def _bubble_depth(state: dict[str, Any], agent: str) -> int:
    """User is 0, coordinator 1, specialists 2. Cards stay siblings."""
    nested = state.get("_nested") or []
    if agent in nested:
        return 2
    hub = str(state.get("_hub") or "")
    if hub and agent == hub:
        return 1
    if hub and agent and agent != hub:
        return 2
    return 1


def _new_bubble(cards: list[dict[str, Any]], agent: str, *, depth: int, t: float | None) -> dict[str, Any]:
    row = {
        "role": "reply",
        "from": agent,
        "text": "",
        "tools": [],
        "t": t,
        "ts": t,
        "depth": depth,
    }
    cards.append(row)
    return row


def _latest_reply(cards: list[dict[str, Any]], agent: str) -> dict[str, Any] | None:
    for row in reversed(cards):
        if row.get("role") == "user":
            break
        if row.get("from") == agent and row.get("role") == "reply":
            return row
    return None


def _later_speaker(cards: list[dict[str, Any]], row: dict[str, Any], agent: str) -> bool:
    """True when another speaker already has a card after this turn."""
    seen = False
    for card in cards:
        if card is row:
            seen = True
            continue
        if not seen:
            continue
        who = str(card.get("from") or "")
        if who and who != agent:
            return True
    return False


def _tool_start_t(tool: dict[str, Any]) -> float | None:
    return _event_t({"timestamp": tool.get("t_start", tool.get("t", tool.get("ts")))})


def _last_tool_start(row: dict[str, Any]) -> float | None:
    tools = row.get("tools") or []
    if not tools:
        return None
    return _tool_start_t(tools[-1])


def _open_turn(
    cards: list[dict[str, Any]],
    agent: str,
    *,
    depth: int,
    t: float | None,
    for_tool: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """One bubble per wave: later speaker, filled reply, time gap, or a new parallel group."""
    row = _latest_reply(cards, agent)
    if row is None or force:
        return _new_bubble(cards, agent, depth=depth, t=t)
    if _later_speaker(cards, row, agent):
        return _new_bubble(cards, agent, depth=depth, t=t)
    if for_tool and str(row.get("text") or "").strip():
        return _new_bubble(cards, agent, depth=depth, t=t)
    if for_tool and t is not None:
        last = _last_tool_start(row)
        if last is not None and abs(float(t) - last) > _WAVE_GAP_S:
            return _new_bubble(cards, agent, depth=depth, t=t)
    return row


def fold_chat_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    kind = str(event.get("kind") or "")
    agent = str(event.get("agent_id") or "agent")
    t = _event_t(event)
    _note_hub(state, event)
    _stamp_t0(state, t)
    cards: list[dict[str, Any]] = list(state.get("cards") or [])
    activity: list[dict[str, Any]] = list(state.get("activity") or [])
    depth = int(state.get("_depth") or 0)
    text = _text(event)
    if _is_human(event, agent) and text:
        if not any(row.get("role") == "user" for row in cards):
            cards.append({"role": "user", "from": "user", "text": text[:4000], "t": t, "ts": t, "depth": 0})
    elif (
        kind == "execution_start"
        and text
        and depth == 0
        and not _looks_like_briefing(text)
        and not any(row.get("role") == "user" for row in cards)
    ):
        cards.append({"role": "user", "from": "user", "text": text[:4000], "t": t, "ts": t, "depth": 0})
    elif kind in {"llm_call_end", "client_response", "user_response"} and text:
        clipped = text[:4000]
        nest = _bubble_depth(state, agent)
        bubble = _open_turn(cards, agent, depth=nest, t=t)
        existing = str(bubble.get("text") or "")
        if existing and existing != clipped:
            same_turn = kind == "user_response" and bubble.get("_from_kind") in {
                "llm_call_end",
                "client_response",
            }
            cid = event.get("call_id")
            if same_turn and cid and bubble.get("call_id") and cid != bubble.get("call_id"):
                same_turn = False
            if same_turn and t is not None and bubble.get("t") is not None:
                same_turn = abs(float(t) - float(bubble["t"])) <= 1.0
            if not same_turn:
                bubble = _new_bubble(cards, agent, depth=nest, t=t)
        bubble["text"] = clipped
        bubble["_from_kind"] = kind
        if event.get("call_id"):
            bubble["call_id"] = event.get("call_id")
        bubble["t"] = t
        bubble["ts"] = t
    elif kind == "parallel_group_start":
        state["_open_group"] = agent
    elif kind == "tool_call_start":
        tool = str(event.get("tool_name") or "")
        delegate = bool(_delegate_peer(tool))
        inflight = int(state.get("_delegates") or 0)
        if delegate:
            inflight += 1
            depth = 1
            peer = _delegate_peer(tool)
            nested = list(state.get("_nested") or [])
            if peer and peer not in nested:
                nested.append(peer)
            state["_nested"] = nested
        force = False
        if delegate and state.pop("_open_group", None) == agent:
            prev = _latest_reply(cards, agent)
            force = prev is not None and bool(prev.get("tools"))
        bubble = _open_turn(
            cards, agent, depth=_bubble_depth(state, agent), t=t, for_tool=True, force=force
        )
        state["_delegates"] = inflight
        tools = list(bubble.get("tools") or [])
        tools.append(
            {
                "role": "tool",
                "from": agent,
                "to": tool,
                "text": text[:4000] if text else "",
                "args": _args(event),
                "tool": tool,
                "kind": "delegate" if delegate else "tool",
                "state": "running",
                "call_id": event.get("call_id"),
                "t_start": t,
                "t": t,
                "ts": t,
            }
        )
        bubble["tools"] = tools
        activity.append(
            {
                "call_id": event.get("call_id"),
                "agent": agent,
                "tool": tool,
                "kind": "delegate" if delegate else "tool",
                "state": "running",
                "t": t,
                "ts": t,
            }
        )
    elif kind == "tool_call_end":
        call_id = event.get("call_id")
        found = False
        for bubble in reversed(cards):
            for row in reversed(list(bubble.get("tools") or [])):
                if call_id and row.get("call_id") == call_id:
                    row["state"] = "done"
                    row["t"] = t
                    row["ts"] = t
                    if text:
                        row["text"] = text[:4000]
                    found = True
                    break
            if found:
                break
        for row in reversed(activity):
            if call_id and row.get("call_id") == call_id:
                row["state"] = "done"
                row["t"] = t
                row["ts"] = t
                if row.get("kind") == "delegate":
                    inflight = max(0, int(state.get("_delegates") or 0) - 1)
                    state["_delegates"] = inflight
                    depth = 1 if inflight or state.get("_nested") else 0
                break
    _retag_depths(state, cards)
    state["cards"] = cards[-200:]
    state["activity"] = activity[-200:]
    state["_depth"] = depth
    state["kind"] = "chat"
    return state


def fold_topology_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Animate the spec graph. Do not invent nodes; spec.snapshot owns the roster."""
    nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in state.get("nodes") or [] if n.get("id")}
    if not nodes:
        return state
    edges: list[dict[str, str]] = list(state.get("edges") or [])
    activity: list[dict[str, Any]] = list(state.get("activity") or [])
    kind = str(event.get("kind") or "")
    agent = str(event.get("agent_id") or "")
    tool = str(event.get("tool_name") or "")
    t = _event_t(event)
    _stamp_t0(state, t)
    if tool.startswith("delegate_to_"):
        peer = tool[len("delegate_to_") :]
        if agent in nodes and peer in nodes:
            activity.append(
                {
                    "from": agent,
                    "to": peer,
                    "state": "running" if kind.endswith("start") else "done",
                    "kind": kind,
                    "t": t,
                    "ts": t,
                }
            )
    elif kind in {
        "llm_call_start",
        "llm_call_end",
        "tool_call_start",
        "tool_call_end",
        "execution_start",
        "execution_end",
        "user_response",
        "client_response",
    }:
        if agent in nodes:
            parent = next(
                (
                    str(row.get("from") or "")
                    for row in edges
                    if str(row.get("to") or "") == agent and str(row.get("from") or "") not in {"", agent}
                ),
                "",
            )
            hop_back = kind in {"user_response", "client_response"} and parent in nodes
            activity.append(
                {
                    "from": agent if hop_back else (agent or "user"),
                    "to": parent if hop_back else agent,
                    "state": "running" if kind.endswith("start") else "done",
                    "kind": kind,
                    "t": t,
                    "ts": t,
                }
            )
    idle = bool(state.get("idle"))
    if kind in {"mas_call_end", "run.terminal"}:
        for row in activity:
            if row.get("state") == "running":
                row["state"] = "done"
                row["t"] = t
                row["ts"] = t
        idle = True
    elif kind.endswith("_start") or kind in {"user_response", "client_response"}:
        idle = False
    state["nodes"] = list(nodes.values())
    state["edges"] = edges
    state["activity"] = activity[-40:]
    state["kind"] = "topology"
    state["idle"] = idle
    return state


def topology_from_spec(snap: dict[str, Any] | None) -> dict[str, Any]:
    """Turn a ``spec.snapshot`` / ``mas_topology`` payload into plot nodes/edges."""
    spec = snap if isinstance(snap, dict) else {}
    user = str(spec.get("user") or "user")
    agents = [agent for agent in (spec.get("agents") or []) if isinstance(agent, dict)]
    delegates = spec.get("delegates_to") if isinstance(spec.get("delegates_to"), dict) else {}
    entry = pick_entry(agents, delegates, str(spec.get("entry") or ""))
    nodes: list[dict[str, Any]] = [{"id": user, "role": "user"}]
    seen = {user}
    for agent in spec.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        aid = str(agent.get("id") or agent.get("name") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        nodes.append({"id": aid, "role": "entry" if aid == entry else "agent"})
    edges: list[dict[str, str]] = []

    def ensure(aid: str, role: str = "agent") -> None:
        aid = str(aid or "").strip()
        if not aid or aid in seen:
            return
        seen.add(aid)
        nodes.append({"id": aid, "role": "entry" if aid == entry else role})

    if entry:
        ensure(entry, "entry")
        edges.append({"from": user, "to": entry})
    for src, peers in (spec.get("delegates_to") or {}).items():
        ensure(str(src), "entry" if str(src) == entry else "agent")
        for peer in peers or []:
            if src and peer:
                ensure(str(peer))
                edges.append({"from": str(src), "to": str(peer)})
    _annotate_depths(nodes, edges)
    return {"kind": "topology", "nodes": nodes, "edges": edges, "activity": []}


def _annotate_depths(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> None:
    children: dict[str, list[str]] = {}
    for edge in edges:
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if src and dst:
            children.setdefault(src, []).append(dst)
    depth: dict[str, int] = {}
    roots = [str(node.get("id") or "") for node in nodes if node.get("role") == "user"]
    if not roots and nodes:
        roots = [str(nodes[0].get("id") or "")]
    queue = [root for root in roots if root]
    for root in queue:
        depth[root] = 0
    while queue:
        cur = queue.pop(0)
        for child in children.get(cur, []):
            if child not in depth:
                depth[child] = depth[cur] + 1
                queue.append(child)
    for node in nodes:
        aid = str(node.get("id") or "")
        node["depth"] = int(depth.get(aid, node.get("depth") or 1))


def load_ui_folds() -> list[Any]:
    """Optional extra folds. mas-lab does not import a detector package here."""
    folds: list[Any] = []
    seen: set[str] = set()

    def add(obj: Any, token: str) -> None:
        if token in seen or obj is None:
            return
        seen.add(token)
        folds.append(obj() if isinstance(obj, type) else obj)

    for spec in str(os.environ.get("MAS_UI_FOLDS") or "").split(","):
        spec = spec.strip()
        if not spec:
            continue
        mod_name, _, attr = spec.partition(":")
        try:
            mod = importlib.import_module(mod_name)
            add(getattr(mod, attr or "Fold"), spec)
        except Exception:
            continue
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        group = (
            eps.select(group="mas.lab.ui.folds")
            if hasattr(eps, "select")
            else eps.get("mas.lab.ui.folds", [])
        )
        for ep in group or []:
            try:
                add(ep.load(), f"ep:{getattr(ep, 'name', ep)}")
            except Exception:
                continue
    except Exception:
        pass
    return folds


def _blob(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _public_fold(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if not str(key).startswith("_")}


class LivePlotFolds:
    """Per-coordinate chat / topology snapshots. Extra folds emit series."""

    def __init__(self) -> None:
        self._chat: dict[str, dict[str, Any]] = {}
        self._topo: dict[str, dict[str, Any]] = {}
        self._extra = load_ui_folds()

    def apply(
        self,
        emit: Callable[[DataSourceRef, dict[str, Any]], None],
        ref: DataSourceRef,
        record: dict[str, Any],
        *,
        skip: set[str] | None = None,
    ) -> None:
        kind = str(record.get("kind") or "")
        if kind.startswith("custom.") or kind.startswith("series."):
            return
        key = ref.coordinate()
        held = skip or set()
        # Reconstruct chat from native events even when a ui-trace snapshot exists.
        self._fold_chat(emit, ref, key, record)
        if "custom.topology" not in held:
            self._fold_topo(emit, ref, key, record)
        for fold in self._extra:
            apply = getattr(fold, "apply", None)
            if apply is None:
                continue
            try:
                apply(emit, ref, record, skip=held)
            except Exception:
                continue

    def seed_spec(self, ref: DataSourceRef, snap: dict[str, Any]) -> dict[str, Any]:
        """Remember topology from a spec snapshot so the first GET is not empty."""
        key = ref.coordinate()
        topo = topology_from_spec(snap)
        self._topo[key] = {**topo, "kind": "topology"}
        return {**topo, "kind": "custom.topology.snapshot"}

    def _fold_chat(
        self,
        emit: Callable[[DataSourceRef, dict[str, Any]], None],
        ref: DataSourceRef,
        key: str,
        record: dict[str, Any],
    ) -> None:
        state = self._chat.setdefault(key, {"kind": "chat", "cards": [], "activity": []})
        prev = _blob(state)
        fold_chat_event(state, record)
        if _blob(state) != prev:
            emit(ref, {**_public_fold(state), "kind": "custom.chat.snapshot"})

    def _fold_topo(
        self,
        emit: Callable[[DataSourceRef, dict[str, Any]], None],
        ref: DataSourceRef,
        key: str,
        record: dict[str, Any],
    ) -> None:
        state = self._topo.setdefault(
            key, {"kind": "topology", "nodes": [], "edges": [], "activity": []}
        )
        prev = _blob(state)
        fold_topology_event(state, record)
        if _blob(state) != prev:
            emit(ref, {**_public_fold(state), "kind": "custom.topology.snapshot"})
