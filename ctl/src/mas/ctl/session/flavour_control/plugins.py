#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve Control observability and governance plugins by id."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mas.ctl.session.flavour_control.documents import control_file
from mas.ctl.session.flavour_control.error import ControlError
from mas.ctl.session.flavour_control.selectors import agent_matches
from mas.ctl.session.flavour_control.spec import GOVERNANCE, OBSERVABILITY


def parse_plugin_item(item: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(item, str):
        name = item.strip()
        if not name:
            raise ControlError("Control plugin item is a plugin id or a one-key config object")
        return name, {}
    if isinstance(item, dict) and len(item) == 1:
        name, raw = next(iter(item.items()))
        name = str(name).strip()
        if not name:
            raise ControlError("Control plugin item is a plugin id or a one-key config object")
        if raw is None:
            return name, {}
        if not isinstance(raw, dict):
            raise ControlError("Control plugin item is a plugin id or a one-key config object")
        return name, dict(raw)
    raise ControlError("Control plugin item is a plugin id or a one-key config object")


def lookup_plugin(name: str, types: frozenset[str] | None = None) -> tuple[str, type]:
    """The plugin's registered type and class. *name* is a URN or shortcut, exactly.

    With *types* (what the list the name is in takes), only plugins of those types are candidates.
    """
    from mas.runtime.registry import get_registry

    registry = get_registry()
    hits = registry.entries_named(name, types)
    if not hits:
        elsewhere = registry.entries_named(name)
        if elsewhere and types is not None:
            kinds = ", ".join(sorted({str(e.attributes.get("plugin_type") or "none") for e in elsewhere}))
            raise ControlError(
                f"plugin {name!r} is registered as type {kinds}; this list takes {' or '.join(sorted(types))}"
            )
        raise ControlError(f"unknown plugin {name!r}")
    if len(hits) > 1:
        urns = ", ".join(entry.urn for entry in hits)
        raise ControlError(f"plugin {name!r} names more than one module ({urns})")
    entry = hits[0]
    kind = str(entry.attributes.get("plugin_type") or "")
    return kind, entry.resolve().load_class()


# Registered types each list accepts.
LIST_TYPES: dict[str, frozenset[str]] = {
    OBSERVABILITY: frozenset({"observability"}),
    GOVERNANCE: frozenset({"governance"}),
    "system_plugins": frozenset({"system_plugin", "system_tool"}),
}


def resolve_pipeline_item(item: Any, *, key: str | None = None) -> dict[str, Any]:
    """Resolve one list item by name to its registry row, params, type and class.

    With *key* (the Control list the item is in), the plugin must be registered under a type that list
    accepts, or the Control is refused.
    """
    name, params = parse_plugin_item(item)
    kind, cls = lookup_plugin(name, None if key is None else LIST_TYPES[key])
    _construct_pipeline_plugin(cls, params)
    if not kind:
        raise ControlError(f"plugin {name!r} has no registered type")
    return {"row": name.rsplit(".", 1)[-1], "params": params, "kind": kind, "cls": cls}


def _construct_pipeline_plugin(cls: type, params: dict[str, Any]) -> None:
    """Build the plugin so constructor fields are checked. Dataclass plugins take their YAML on a binding object
    later, and a relative path resolves against the Control directory when the session reads it.
    """

    from mas.runtime.registry import construct_plugin
    from mas.runtime.spec.gov import SpecBindingError

    params = dict(params)
    params.pop("priority", None)
    if dataclasses.is_dataclass(cls):
        return
    try:
        construct_plugin(cls, params)
    except FileNotFoundError:
        pass
    except (SpecBindingError, TypeError, ValueError) as exc:
        raise ControlError(str(exc)) from exc


def plugin_entry(item: Any) -> dict[str, Any]:
    """Resolve one list item to its registry row, params, type, and class."""
    return resolve_pipeline_item(item)


def _as_plugin_list(raw: Any) -> list[Any]:
    if not raw:
        return []
    return list(raw) if isinstance(raw, list) else [raw]


def canonical_plugin_id(name: str, key: str | None = None) -> str:
    """The URN of the plugin *name* stands for, so two spellings of one plugin are one id.

    *key* is the Control list (``observability`` or ``governance``): it says which plugin type a name
    two types share (``native``) means. Without it, either list type counts. A name that is not
    exactly one plugin is returned as written; loading refuses it elsewhere.
    """
    text = str(name or "").strip()
    if not text:
        return text
    from mas.runtime.registry import get_registry

    types = LIST_TYPES[key] if key else frozenset().union(*LIST_TYPES.values())
    hits = get_registry().entries_named(text, tuple(types))
    if len(hits) == 1:
        return hits[0].urn
    return text


def plugin_item_id(item: Any, key: str | None = None) -> str:
    name, _cfg = parse_plugin_item(item)
    return canonical_plugin_id(name, key)


def plugin_priority(item: Any) -> int:
    """Attachment priority (higher first). Default 0. A non-integer is refused."""
    from mas.runtime.spec.gov import SpecBindingError
    from mas.runtime.spec.gov import plugin_priority as checked

    name, cfg = parse_plugin_item(item)
    try:
        return checked(cfg)
    except SpecBindingError as exc:
        raise ControlError(f"plugin {name!r}: {exc}") from exc


def union_plugin_set(lists: Sequence[Sequence[Any]], key: str | None = None) -> list[Any]:
    """Set union by plugin id, resolved first: ``native`` and its URN are one plugin. Later config wins."""
    by_id: dict[str, Any] = {}
    order: list[str] = []
    for items in lists:
        for item in items:
            name = plugin_item_id(item, key)
            if name not in by_id:
                order.append(name)
            by_id[name] = item
    return [by_id[name] for name in order]


def union_observability(lists: Sequence[Sequence[Any]]) -> list[Any]:
    """Set union by plugin id. A later Control's config for the same id wins."""
    return union_plugin_set(lists, OBSERVABILITY)


def sort_governance_set(items: Sequence[Any]) -> list[Any]:
    """Higher ``priority`` first; same priority is the plugin id."""
    return sorted(items, key=lambda item: (-plugin_priority(item), plugin_item_id(item, GOVERNANCE)))


def duplicate_plugin_id_findings(spec: dict[str, Any] | None) -> list[tuple[str, str]]:
    """``(path, message)`` for a plugin id that appears twice in one Control set."""
    findings: list[tuple[str, str]] = []
    body = spec if isinstance(spec, dict) else {}
    for key in (OBSERVABILITY, GOVERNANCE):
        seen: set[str] = set()
        for index, item in enumerate(_as_plugin_list(body.get(key))):
            try:
                name = plugin_item_id(item, key)
            except ControlError:
                continue
            if name in seen:
                findings.append((f"spec.{key}[{index}]", f"plugin {name!r} is already in the set"))
            else:
                seen.add(name)
    return findings


def priority_findings(spec: dict[str, Any] | None) -> list[tuple[str, str]]:
    """``(path, message)`` for a plugin ``priority`` that is not an integer."""
    findings: list[tuple[str, str]] = []
    body = spec if isinstance(spec, dict) else {}
    for key in (OBSERVABILITY, GOVERNANCE):
        for index, item in enumerate(_as_plugin_list(body.get(key))):
            try:
                plugin_priority(item)
            except ControlError as exc:
                findings.append((f"spec.{key}[{index}]", str(exc)))
    return findings


def keyed_items(body: dict[str, Any]) -> list[tuple[str, Any]]:
    """``(list key, item)`` for every plugin in a Control body, for load checks."""
    return [(key, item) for key in (OBSERVABILITY, GOVERNANCE) for item in _as_plugin_list(body.get(key))]


def _plugin_path_attrs(name: str, key: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``path_params`` (files next to the Control) and ``run_path_params`` (paths the run writes)."""
    from mas.runtime.registry import get_registry

    hits = get_registry().entries_named(name, tuple(LIST_TYPES[key]))
    if len(hits) != 1:
        return (), ()
    attrs = hits[0].attributes
    return (
        tuple(str(p) for p in attrs.get("path_params") or ()),
        tuple(str(p) for p in attrs.get("run_path_params") or ()),
    )


def _path_params(name: str, key: str) -> tuple[str, ...]:
    """Parameters a plugin declares as files next to the Control (``attributes.path_params``)."""
    return _plugin_path_attrs(name, key)[0]


_FILE_SUFFIX = {".gdb", ".py", ".yaml", ".yml", ".json", ".jsonl", ".txt", ".sh", ".md"}


def _looks_like_relative_path(text: str) -> bool:
    from mas.ctl.session.flavour_control.refs import _URL_SCHEME, is_absolute_path

    raw = text[1:] if text.startswith("@") else text
    if not raw or is_absolute_path(raw) or _URL_SCHEME.match(raw):
        return False
    return "/" in raw.replace("\\", "/") or Path(raw).suffix.lower() in _FILE_SUFFIX


def undeclared_path_findings(spec: dict[str, Any] | None) -> list[tuple[str, str]]:
    """``(path, message)`` for a relative path whose key is in neither path list."""
    findings: list[tuple[str, str]] = []
    body = spec if isinstance(spec, dict) else {}
    for key in (OBSERVABILITY, GOVERNANCE):
        for index, item in enumerate(_as_plugin_list(body.get(key))):
            try:
                name, cfg = parse_plugin_item(item)
            except ControlError:
                continue
            control_paths, run_paths = _plugin_path_attrs(name, key)
            listed = {*control_paths, *run_paths}
            for param, value in cfg.items():
                if isinstance(value, str) and _looks_like_relative_path(value) and param not in listed:
                    findings.append(
                        (
                            f"spec.{key}[{index}].{param}",
                            f"plugin {name!r} parameter {param!r} looks like a path; "
                            "list it in attributes.path_params (a file next to the Control) "
                            "or attributes.run_path_params (a path the run writes)",
                        )
                    )
    return findings


def _items_for_session(raw: Any, control: dict[str, Any], key: str) -> list[Any]:
    """Plugin list for session parsers.

    A plugin names its path-valued parameters in its library row; a relative one resolves against the
    Control file's directory here, in the session view. The Control document is not changed and no
    directory enters the spec: where files live is the loader's business, not a plugin setting.
    """
    file = control_file(control)
    base = Path(file).parent if file else None
    out: list[Any] = []
    for item in _as_plugin_list(raw):
        if base is None or not base.is_dir() or not isinstance(item, dict):
            out.append(item)
            continue
        name, cfg = parse_plugin_item(item)
        paths = [p for p in _path_params(name, key) if isinstance(cfg.get(p), str) and not Path(cfg[p]).is_absolute()]
        if not paths:
            out.append(item)
            continue
        out.append({name: {**cfg, **{p: str(base / cfg[p]) for p in paths}}})
    return out


def control_lists_for_agent(
    controls: Sequence[dict[str, Any]],
    *,
    agent_id: str,
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
) -> dict[str, list[Any]]:
    """Matching Controls: observability and governance are set unions.

    A later Control's config for the same plugin id wins. Governance order on a transition is each
    plugin's ``priority`` (higher first, default 0; same priority is the plugin id).
    Lists stay on the Control. Callers read them here; they are not written
    onto the agent spec. A key is present only when a matching Control names
    it, so an absent key leaves the agent's leftover list in force.
    """
    named: dict[str, list[list[Any]]] = {OBSERVABILITY: [], GOVERNANCE: []}
    for control in matching_controls(controls, agent_id=agent_id, entry_id=entry_id, delegate_ids=delegate_ids):
        spec = control.get("spec") or {}
        for key, lists in named.items():
            if key in spec:
                lists.append(_items_for_session(spec[key], control, key))
    out: dict[str, Any] = {}
    for key, lists in named.items():
        if lists:
            union = union_plugin_set(lists, key)
            out[key] = union if key == OBSERVABILITY else sort_governance_set(union)
    return out


def matching_controls(
    controls: Sequence[dict[str, Any]],
    *,
    agent_id: str,
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Matching Control rows, each file once, in the order they attach."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for control in controls:
        if not agent_matches(
            control.get("agents"),
            agent_id=agent_id,
            entry_id=entry_id,
            delegate_ids=delegate_ids,
        ):
            continue
        file = control_file(control)
        # One file attaches once to an agent; the same file with other settings is another attachment.
        key = json.dumps([file, control.get("settings") or {}], sort_keys=True) if file else ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(control)
    return out
