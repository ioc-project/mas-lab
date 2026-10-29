#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Build a MAS topology snapshot for the demo-ui spec stream."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mas.runtime.boundary.delegation.policy import parse_delegate_tool_name


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _skill_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or item.get("id") or item.get("ref") or "").strip()
    return str(item or "").strip()


def _tool_name(item: Any, mas_dir: Path | None = None) -> str:
    if isinstance(item, str):
        text = item.strip()
        if text.endswith(".yaml") or text.endswith(".yml"):
            return Path(text).stem
        return text
    if isinstance(item, dict):
        named = str(item.get("name") or item.get("id") or "").strip()
        if named:
            return named
        ref = str(item.get("ref") or "").strip()
        if ref:
            return Path(ref).stem
    return ""


def _agent_skills(doc: dict[str, Any]) -> list[str]:
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else doc
    names: list[str] = []
    for item in _as_list((spec or {}).get("skills")):
        name = _skill_name(item)
        if name:
            names.append(name)
    return names


def _agent_tools(doc: dict[str, Any]) -> list[str]:
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else doc
    names: list[str] = []
    for item in _as_list((spec or {}).get("tools")):
        name = _tool_name(item)
        if name:
            names.append(name)
    return names


def _overlay_skill_adds(overlay: dict[str, Any] | None) -> dict[str, list[str]]:
    if not isinstance(overlay, dict):
        return {}
    patch = ((overlay.get("spec") or {}).get("patch") or {}).get("agents") or {}
    if not isinstance(patch, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, body in patch.items():
        if not isinstance(body, dict):
            continue
        skills = body.get("skills") or {}
        added = skills.get("$op", {}).get("add") if isinstance(skills, dict) else None
        if added is None and isinstance(skills, dict):
            added = skills.get("add")
        names = [_skill_name(item) for item in _as_list(added)]
        names = [name for name in names if name]
        if names:
            out[str(key)] = names
    return out


def apply_overlay_skills(
    agents: list[dict[str, Any]],
    overlay: dict[str, Any] | None,
    entry_id: str,
) -> None:
    adds = _overlay_skill_adds(overlay)
    if not adds:
        return
    by_id = {str(item["id"]): item for item in agents}
    for key, names in adds.items():
        targets: list[str]
        if key in {"$all", "all"}:
            targets = list(by_id)
        elif key in {"$entry", "entry"}:
            targets = [entry_id] if entry_id in by_id else []
        elif key in {"$not-entry", "not-entry"}:
            targets = [aid for aid in by_id if aid != entry_id]
        else:
            targets = [key] if key in by_id else []
        for aid in targets:
            have = list(by_id[aid].get("skills") or [])
            for name in names:
                if name not in have:
                    have.append(name)
            by_id[aid]["skills"] = have


def snapshot_from_documents(
    mas_doc: dict[str, Any],
    agent_docs: dict[str, dict[str, Any]] | None = None,
    overlay_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a UI topology snapshot: entry, agents, tools, skills, workflow edges."""
    spec = mas_doc.get("spec") if isinstance(mas_doc.get("spec"), dict) else mas_doc
    agency = (spec or {}).get("agency") or {}
    workflow = (spec or {}).get("workflow") or {}
    entry = str(workflow.get("entry") or "").strip()
    listed = _as_list(agency.get("agents"))
    agent_docs = agent_docs or {}
    agents: list[dict[str, Any]] = []
    delegates: dict[str, list[str]] = {}
    for item in listed:
        if isinstance(item, str):
            aid = item
        elif isinstance(item, dict):
            aid = str(item.get("id") or item.get("name") or "").strip()
        else:
            continue
        if not aid:
            continue
        if not entry:
            entry = aid
        doc = agent_docs.get(aid) or {}
        agents.append(
            {
                "id": aid,
                "tools": _agent_tools(doc),
                "skills": _agent_skills(doc),
            }
        )
    for node in _as_list(workflow.get("nodes")):
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "").strip()
        peers = [str(p).strip() for p in _as_list(node.get("delegates_to")) if str(p).strip()]
        if nid and peers:
            delegates[nid] = peers
    apply_overlay_skills(agents, overlay_doc, entry)
    return {
        "kind": "mas_topology",
        "entry": entry,
        "user": "user",
        "agents": agents,
        "delegates_to": delegates,
    }


_SKIP_ENTRY = frozenset({"", "mas", "user", "agent"})
_HUB_HINTS = frozenset({"coordinator", "orchestrator", "planner"})
_HUB_TOOLS = frozenset({"activate_skill", "run_action"})


def infer_implicit_delegates(
    agents: list[dict[str, Any]],
    records: list[dict[str, Any]] | None = None,
    hinted: str = "",
) -> tuple[str, dict[str, list[str]]]:
    """Star from the coordinator when the trace has no ``delegate_to_*`` tools."""
    ids = [
        str(agent.get("id") or "").strip()
        for agent in agents
        if isinstance(agent, dict) and str(agent.get("id") or "").strip() not in _SKIP_ENTRY
    ]
    ids = [aid for aid in ids if aid]
    if len(ids) < 2:
        return "", {}
    hub = next((aid for aid in ids if aid.lower() in _HUB_HINTS), "")
    last_speaker = ""
    for record in records or []:
        if not isinstance(record, dict):
            continue
        aid = str(record.get("agent_id") or "").strip()
        if aid not in ids:
            continue
        kind = str(record.get("kind") or "")
        tool = str(record.get("tool_name") or "")
        if kind == "tool_call_start" and tool in _HUB_TOOLS:
            hub = aid
        if kind in {"user_response", "llm_call_end", "client_response"}:
            last_speaker = aid
    if not hub:
        hinted = str(hinted or "").strip()
        hub = hinted if hinted in ids else last_speaker or ids[0]
    others = [aid for aid in ids if aid != hub]
    return hub, ({hub: others} if hub and others else {})


def pick_entry(
    agents: list[dict[str, Any]],
    delegates: dict[str, list[str]],
    hinted: str = "",
) -> str:
    """Entry is the unique delegate hub, not the first native agent_id (often ``mas``)."""
    children = {str(peer) for peers in (delegates or {}).values() for peer in (peers or [])}
    roots = [
        str(src)
        for src in (delegates or {})
        if str(src) not in children and str(src) not in _SKIP_ENTRY
    ]
    if len(roots) == 1:
        return roots[0]
    hinted = str(hinted or "").strip()
    ids = {str(agent.get("id") or "") for agent in agents if isinstance(agent, dict)}
    if hinted not in _SKIP_ENTRY and hinted in ids:
        return hinted
    if roots:
        return roots[0]
    for agent in agents:
        aid = str((agent or {}).get("id") or "")
        if aid not in _SKIP_ENTRY:
            return aid
    return ""


def snapshot_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Infer a topology snapshot from native events (no MAS YAML required)."""
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    hinted = ""
    delegates: dict[str, list[str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = str(record.get("kind") or "")
        aid = str(record.get("agent_id") or "").strip()
        if aid and aid not in {"mas", "user"} and aid not in seen:
            seen.add(aid)
            agents.append({"id": aid, "tools": [], "skills": []})
        if not hinted and aid not in _SKIP_ENTRY and kind in {"execution_start", "mas_call_start"}:
            hinted = aid
        if kind != "tool_call_start":
            continue
        tool = str(record.get("tool_name") or "")
        peer = parse_delegate_tool_name(tool)
        if not peer or not aid:
            continue
        peers = delegates.setdefault(aid, [])
        if peer and peer not in peers:
            peers.append(peer)
        if peer and peer not in seen:
            seen.add(peer)
            agents.append({"id": peer, "tools": [], "skills": []})
    if not delegates and len(agents) > 1:
        hub, delegates = infer_implicit_delegates(agents, records, hinted)
        if hub:
            hinted = hub
    return {
        "kind": "mas_topology",
        "entry": pick_entry(agents, delegates, hinted),
        "user": "user",
        "agents": agents,
        "delegates_to": delegates,
    }


def merge_spec_snapshots(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    """Union agents and delegates_to so a sparse MAS YAML still shows event peers."""
    out = dict(base or {})
    extra = extra or {}
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src in (out.get("agents"), extra.get("agents")):
        for agent in src or []:
            if not isinstance(agent, dict):
                continue
            aid = str(agent.get("id") or "").strip()
            if not aid or aid in seen:
                continue
            seen.add(aid)
            agents.append(agent)
    delegates: dict[str, list[str]] = {}
    for src in (out.get("delegates_to"), extra.get("delegates_to")):
        if not isinstance(src, dict):
            continue
        for key, peers in src.items():
            have = delegates.setdefault(str(key), [])
            for peer in peers or []:
                name = str(peer).strip()
                if name and name not in have:
                    have.append(name)
                if name and name not in seen:
                    seen.add(name)
                    agents.append({"id": name, "tools": [], "skills": []})
            if str(key) not in seen:
                seen.add(str(key))
                agents.append({"id": str(key), "tools": [], "skills": []})
    out["agents"] = agents
    out["delegates_to"] = delegates
    out["entry"] = pick_entry(agents, delegates, str(out.get("entry") or extra.get("entry") or ""))
    out.setdefault("user", extra.get("user") or out.get("user") or "user")
    out.setdefault("kind", extra.get("kind") or "mas_topology")
    return out


def load_yaml_text(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text) or {}
    return data if isinstance(data, dict) else {}
