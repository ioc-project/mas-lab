#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agency groups: implicit ones from ``count``, explicit ones written in the spec.

An entry ``id: worker, count: n`` implicitly creates the group ``worker`` and the agents ``worker-1`` ..
``worker-n``, each from the entry's ``ref`` (or inline definition). Nothing is stored for it: it is read off
the entry. An explicit group is written by the author, ``spec.agency.groups: {reviewers: [critic, verifier]}``:
a name and its members, which are agency agents or implicit groups (no nesting).

``delegates_to`` and a workflow node whose id is a group expand to every member. Single-agent fields
(``workflow.entry``, ``spec.entry_agent``, ``node.agent``) accept a group only when it has one member.
Overlay patches, Control selectors and Flavour ``bind.agents`` that name a group apply to its members.
Expansion materializes every group, implicit ones included, as an explicit ``spec.agency.groups`` entry of the
expanded document. The runtime only ever sees the concrete agents.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from mas.ctl.infra.identities import agency_row_id

# The largest ``count``. A sweep typo must fail here, not after thousands of agents are loaded.
MAX_COUNT = 256


class AgencyGroupError(ValueError):
    """A ``count`` entry, or a reference to a count group, is not valid."""


def _is_inline(entry: dict[str, Any]) -> bool:
    return str(entry.get("kind", "")).lower() == "agent" and isinstance(entry.get("spec"), dict)


def check_count(value: Any, gid: str) -> int:
    """*value* as a count, or :class:`AgencyGroupError` when it is not an integer in ``1..MAX_COUNT``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgencyGroupError(f"agency agent {gid!r}: count must be an integer, got {value!r}")
    if value < 1 or value > MAX_COUNT:
        raise AgencyGroupError(f"agency agent {gid!r}: count must be between 1 and {MAX_COUNT}, got {value}")
    return value


def _agency(spec: dict[str, Any]) -> dict[str, Any] | None:
    agency = spec.get("agency")
    return agency if isinstance(agency, dict) and isinstance(agency.get("agents"), list) else None


def member_ids(group: str, count: int) -> list[str]:
    return [f"{group}-{index}" for index in range(1, count + 1)]


def _copy_for_member(entry: dict[str, Any], member: str, group: str) -> dict[str, Any]:
    row = copy.deepcopy(entry)
    row.pop("count", None)
    if _is_inline(row):
        row.setdefault("metadata", {})["name"] = member
        for key in ("id", "name"):
            if key in row:
                row[key] = member
        return row
    row["id"] = member
    row["group"] = group
    row.pop("name", None)
    return row


def _implicit_groups(rows: list[Any]) -> dict[str, list[str]]:
    """The groups made by ``count`` entries. Raises on an invalid entry."""
    groups: dict[str, list[str]] = {}
    for entry in rows:
        if not isinstance(entry, dict) or "count" not in entry:
            continue
        gid = agency_row_id(entry)
        if not gid:
            raise AgencyGroupError("agency entry with count has no id")
        count = check_count(entry["count"], gid)
        if gid in groups:
            raise AgencyGroupError(f"agency group {gid!r} is declared twice")
        ref = entry.get("ref")
        if not _is_inline(entry) and not (isinstance(ref, str) and ref.strip()):
            raise AgencyGroupError(
                f"agency agent {gid!r} has count but no ref; count needs a ref or an inline Agent definition"
            )
        groups[gid] = member_ids(gid, count)
    return groups


def _explicit_groups(authored: Any, agent_ids: set[str], implicit: dict[str, list[str]]) -> dict[str, list[str]]:
    """The authored groups with implicit members resolved. *agent_ids* are the agents after expansion."""
    if authored is None:
        return {}
    if not isinstance(authored, dict):
        raise AgencyGroupError("spec.agency.groups must map a group id to its members")
    out: dict[str, list[str]] = {}
    for gid, members in authored.items():
        gid = str(gid)
        if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
            raise AgencyGroupError(f"spec.agency.groups.{gid} must be a non-empty list of agent or group ids")
        if gid in implicit:
            raise AgencyGroupError(f"agency group {gid!r} is declared twice (count and spec.agency.groups)")
        if gid in agent_ids:
            raise AgencyGroupError(f"agency agent {gid!r} is both a group and a declared agent")
        resolved: list[str] = []
        for member in members:
            if member in authored:
                raise AgencyGroupError(f"spec.agency.groups.{gid} names group {member!r}; groups do not nest")
            expanded = implicit.get(member, [member])
            unknown = [m for m in expanded if m not in agent_ids]
            if unknown:
                raise AgencyGroupError(f"spec.agency.groups.{gid} names agents that are not in the agency: {unknown}")
            resolved.extend(m for m in expanded if m not in resolved)
        out[gid] = resolved
    return out


def _all_groups(rows: list[Any], authored: Any) -> dict[str, list[str]]:
    implicit = _implicit_groups(rows)
    generated = {m for members in implicit.values() for m in members}
    ids = {agency_row_id(r) for r in rows if isinstance(r, dict) and "count" not in r} | generated
    return {**implicit, **_explicit_groups(authored, {i for i in ids if i}, implicit)}


def expand_agency_groups(mas: dict[str, Any]) -> dict[str, Any]:
    """Return *mas* with groups expanded. A document without ``count`` or ``groups`` comes back unchanged.

    Raises :class:`AgencyGroupError` for an invalid count or group, a missing ref, a generated id that
    collides with another agent or a workflow node, a group named as the entry agent with more than one
    member, or ``count`` on the short-form ``spec.agents``.
    """
    spec = mas.get("spec")
    if not isinstance(spec, dict):
        return mas
    short_form = spec.get("agents")
    if isinstance(short_form, list) and any(isinstance(e, dict) and "count" in e for e in short_form):
        raise AgencyGroupError("count is valid only on spec.agency.agents entries, not on spec.agents")
    agency = _agency(spec)
    if agency is None:
        raw = spec.get("agency")
        if isinstance(raw, dict) and raw.get("groups"):
            raise AgencyGroupError("spec.agency.groups needs a spec.agency.agents list")
        return mas
    rows = agency["agents"]
    authored = agency.get("groups")
    if not authored and not any(isinstance(e, dict) and "count" in e for e in rows):
        return mas

    implicit = _implicit_groups(rows)
    new_rows: list[Any] = []
    for entry in rows:
        if isinstance(entry, dict) and "count" in entry:
            new_rows.extend(_copy_for_member(entry, m, agency_row_id(entry)) for m in implicit[agency_row_id(entry)])
        else:
            new_rows.append(copy.deepcopy(entry))
    _check_collisions(new_rows, implicit)
    groups = _all_groups(rows, authored)

    out = copy.deepcopy(mas)
    _rewrite_references(out["spec"], groups)
    out["spec"]["agency"]["agents"] = new_rows
    out["spec"]["agency"]["groups"] = groups
    return mas if out == mas else out


def _check_collisions(rows: list[Any], groups: dict[str, list[str]]) -> None:
    counts = Counter(agency_row_id(row) for row in rows if isinstance(row, dict))
    generated = {member: gid for gid, members in groups.items() for member in members}
    for aid, seen in counts.items():
        if not aid:
            continue
        if aid in groups:
            raise AgencyGroupError(f"agency agent {aid!r} is both a count group and a declared agent")
        if aid in generated and seen > 1:
            raise AgencyGroupError(
                f"agency agent id {aid!r} is generated by count group {generated[aid]!r} and also declared"
            )


def _rewrite_references(spec: dict[str, Any], groups: dict[str, list[str]]) -> None:
    def targets(names: list[Any]) -> list[Any]:
        out: list[Any] = []
        for name in names:
            expanded = groups.get(name, [name]) if isinstance(name, str) else [name]
            for item in expanded:
                if item not in out:
                    out.append(item)
        return out

    def single(name: Any, where: str) -> Any:
        if not isinstance(name, str) or name not in groups:
            return name
        members = groups[name]
        if len(members) > 1:
            raise AgencyGroupError(
                f"{where} names count group {name!r} with {len(members)} agents; it must be a single agent"
            )
        return members[0]

    workflow = spec.get("workflow")
    if isinstance(workflow, dict):
        if "entry" in workflow:
            workflow["entry"] = single(workflow["entry"], "workflow.entry")
        nodes = workflow.get("nodes")
        if isinstance(nodes, list):
            workflow["nodes"] = _rewrite_nodes(nodes, groups, targets, single)
    if "entry_agent" in spec:
        spec["entry_agent"] = single(spec["entry_agent"], "spec.entry_agent")


def _rewrite_nodes(nodes: list[Any], groups: dict[str, list[str]], targets: Any, single: Any) -> list[Any]:
    new_nodes: list[Any] = []
    for node in nodes:
        if not isinstance(node, dict):
            new_nodes.append(node)
            continue
        if isinstance(node.get("delegates_to"), list):
            node["delegates_to"] = targets(node["delegates_to"])
        nid = node.get("id")
        agent = node.get("agent")
        if isinstance(nid, str) and nid in groups:
            if isinstance(agent, str) and agent in groups and agent != nid:
                raise AgencyGroupError(
                    f"workflow node {nid!r} agent names count group {agent!r}; "
                    "a group node cannot bind a different count group"
                )
            for member in groups[nid]:
                copy_node = copy.deepcopy(node)
                copy_node["id"] = member
                if isinstance(agent, str) and agent == nid:
                    copy_node["agent"] = member
                new_nodes.append(copy_node)
            continue
        if isinstance(agent, str) and agent in groups:
            node = copy.deepcopy(node)
            node["agent"] = single(agent, f"workflow node {nid!r} agent")
        new_nodes.append(node)
    duplicated = sorted(
        k for k, n in Counter(n.get("id") for n in new_nodes if isinstance(n, dict)).items() if k and n > 1
    )
    if duplicated:
        raise AgencyGroupError(f"workflow node ids {duplicated} appear more than once after expanding count groups")
    return new_nodes


def loaded_session_mas(document: dict[str, Any] | None) -> dict[str, Any] | None:
    """A loaded MAS as a session sees it: ``count`` entries expanded. Other kinds are unchanged."""
    if not isinstance(document, dict) or str(document.get("kind") or "") != "MAS":
        return document
    return expand_agency_groups(document)


def group_members(mas: dict[str, Any] | None) -> dict[str, list[str]]:
    """Group id to its agent ids: the ``count`` groups and the authored ones, read from a MAS document."""
    spec = mas.get("spec") if isinstance(mas, dict) and isinstance(mas.get("spec"), dict) else {}
    agency = _agency(spec)
    return _all_groups(agency["agents"], agency.get("groups")) if agency else {}


def remove_from_groups(agency: dict[str, Any], named: set[str]) -> set[str]:
    """Apply an overlay ``remove`` of *named* ids to ``agency.groups`` and return the agent ids to remove.

    A group id stands for its members. Removed agents leave every explicit group, and a group left
    empty goes too (a reference to it then fails as unknown instead of expanding to nothing).
    """
    members = group_members({"spec": {"agency": agency}}) if _agency({"agency": agency}) else {}
    removed = set(named)
    for gid in named:
        removed.update(members.get(gid, ()))
    authored = agency.get("groups")
    if isinstance(authored, dict):
        kept = {}
        for gid, listed in authored.items():
            left = [m for m in listed if m not in removed] if isinstance(listed, list) else listed
            if str(gid) not in named and left:
                kept[gid] = left
        if kept:
            agency["groups"] = kept
        else:
            agency.pop("groups", None)
    return removed


def declared_group_ids(spec: dict[str, Any]) -> set[str]:
    """Group ids of a MAS spec: the entries that set ``count`` and the authored groups."""
    agency = _agency(spec)
    if agency is None:
        return set()
    ids = {agency_row_id(row) for row in agency["agents"] if isinstance(row, dict) and "count" in row}
    authored = agency.get("groups")
    if isinstance(authored, dict):
        ids.update(str(g) for g in authored)
    return {gid for gid in ids if gid}


def name_group_member(
    agent_manifest: dict[str, Any], *, agent_id: str, mas_config: dict[str, Any] | None
) -> dict[str, Any]:
    """Name a loaded Agent after its concrete id when its row is a copy made by ``count``.

    Copies share one definition, hence one name. Expansion marks a copy row with ``group``.
    Members of an explicit group keep their own names.
    """
    agency = _agency((mas_config or {}).get("spec") or {})
    rows = agency["agents"] if agency else []
    if not any(isinstance(r, dict) and r.get("group") and agency_row_id(r) == agent_id for r in rows):
        return agent_manifest
    out = copy.deepcopy(agent_manifest)
    out.setdefault("metadata", {})["name"] = agent_id
    return out


def agency_group_findings(mas: dict[str, Any]) -> list[str]:
    """Messages for the count and group rules, for ``mas-ctl validate``. Empty when the document is valid."""
    try:
        expand_agency_groups(mas)
    except AgencyGroupError as exc:
        return [str(exc)]
    return []
