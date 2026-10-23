"""Apply CLI overrides by generating and merging canonical overlays."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from mas.ctl.overlay.merge import apply_identity_merge_patch, merge_overlay
from mas.ctl.overrides.model import OverridePath, ParsedOverride, PathSegment, identity_pair
from mas.ctl.overrides.parser import parse_override
from mas.ctl.overrides.schema import (
    assert_cli_allowed,
    assert_path_declared,
    load_target_schema,
)
from mas.ctl.validate.schemas import load_schema

_ROOT_TARGETS = {
    "agent": "Agent",
    "mas": "MAS",
    "infra": "Infra",
    "flavour": "Flavour",
    "experiment": "Experiment",
    "workspace": "Workspace",
}


def _standalone_agent_name(document: dict[str, Any]) -> str | None:
    meta = document.get("metadata")
    name = meta.get("name") if isinstance(meta, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    ident = document.get("id")
    return str(ident).strip() if ident else None


def _require_standalone_target(document: dict[str, Any], override: ParsedOverride) -> None:
    path = override.path
    if path.parent or path.root != "agent" or not path.target_name:
        return
    actual = _standalone_agent_name(document)
    if actual and actual != path.target_name:
        raise ValueError(
            f"this session is standalone agent {actual!r}; "
            f"{override.source!r} selects {path.target_name!r}"
        )


def _attach_identity(item: Any, body: dict[str, Any]) -> dict[str, Any]:
    attached = deepcopy(body)
    ident = identity_pair(item)
    if ident is not None:
        attached.setdefault(ident[0], ident[1])
    return attached


def _live_child(live: Any, segment: PathSegment) -> Any:
    if not isinstance(live, dict):
        return None
    value = live.get(segment.name)
    if segment.map_key is not None:
        return value.get(segment.map_key) if isinstance(value, dict) else None
    if segment.selector is None and not segment.wildcard:
        return value
    if not isinstance(value, list):
        return None
    indexes = _selected_indexes(value, segment)
    if len(indexes) == 1:
        return value[indexes[0]]
    return None


def _content_patch(segments: list[PathSegment], value: Any, live: Any = None) -> Any:
    """Levels 2+ of a session path as Overlay ``spec.patch``."""
    if not segments:
        return deepcopy(value)
    segment, *rest = segments
    child_live = _live_child(live, segment)
    inner = _content_patch(rest, value, child_live)
    if segment.map_key is not None:
        return {segment.name: {segment.map_key: inner}}
    if segment.selector is None and not segment.wildcard:
        return {segment.name: inner}
    if segment.wildcard:
        items = live.get(segment.name) if isinstance(live, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError(f"no {segment.name!r} entry matches *")
        body = inner if isinstance(inner, dict) else {}
        return {segment.name: [_attach_identity(item, body) for item in items]}
    if isinstance(segment.selector, int):
        items = live.get(segment.name) if isinstance(live, dict) else None
        if not isinstance(items, list) or not (0 <= segment.selector < len(items)):
            raise ValueError(f"no {segment.name!r} entry matches {segment.selector_text}")
        body = inner if isinstance(inner, dict) else {}
        if not rest:
            body = deepcopy(value) if isinstance(value, dict) else body
        return {segment.name: [_attach_identity(items[segment.selector], body)]}
    if not isinstance(segment.selector, tuple):
        raise ValueError(f"no {segment.name!r} entry matches {segment.selector_text}")
    key, expected = segment.selector
    if rest:
        body = inner if isinstance(inner, dict) else {}
    elif isinstance(inner, dict):
        body = deepcopy(inner)
    else:
        raise ValueError(f"creating {key}={expected!r} on {segment.name!r} requires a mapping value")
    body.setdefault(key, expected)
    return {segment.name: [body]}


def _matches(item: Any, selector: int | tuple[str, Any]) -> bool:
    if isinstance(selector, int):
        return False
    key, expected = selector
    return isinstance(item, dict) and item.get(key) == expected


def _selected_indexes(items: list[Any], segment: PathSegment) -> list[int]:
    if segment.wildcard:
        return list(range(len(items)))
    if isinstance(segment.selector, int):
        if segment.selector >= len(items):
            raise ValueError(f"index {segment.selector} is out of range for {segment.name!r}")
        return [segment.selector]
    if segment.selector is None:
        return list(range(len(items)))
    return [index for index, item in enumerate(items) if _matches(item, segment.selector)]


def _spec_overlay_path(path: OverridePath) -> bool:
    return bool(path.segments) and path.segments[0].name == "spec"


def _uses_op(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("$op"), dict)


def _overlay_document(target_kind: str, path: OverridePath, patch: dict[str, Any], **extra: Any) -> dict[str, Any]:
    target: dict[str, Any] = {"kind": target_kind}
    if path.target_name:
        target["name"] = path.target_name
    spec: dict[str, Any] = {"target": target, "patch": patch, **extra}
    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "cli-override"},
        "spec": spec,
    }


def _patch_segments(path: OverridePath) -> list[PathSegment]:
    segments = list(path.segments)
    if segments and segments[0].name == "spec":
        return segments[1:]
    return segments


def _lift_mas_agency_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """``agency.agents[id].spec…`` is Overlay ``patch.agents.<id>`` (levels 2+)."""
    agency = patch.get("agency")
    if not isinstance(agency, dict):
        return patch
    rows = agency.get("agents")
    if not isinstance(rows, list) or not rows:
        return patch
    lifted: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            return patch
        body = {key: value for key, value in row.items() if key != "id"}
        if list(body.keys()) == ["spec"] and isinstance(body["spec"], dict):
            body = body["spec"]
        lifted[str(row["id"])] = body
    out = {key: value for key, value in patch.items() if key != "agency"}
    leftover = {key: value for key, value in agency.items() if key != "agents"}
    if leftover:
        out["agency"] = leftover
    existing = out.get("agents")
    if isinstance(existing, dict):
        out["agents"] = {**existing, **lifted}
    else:
        out["agents"] = lifted
    return out


def _overlay_for_override(document: dict[str, Any], override: ParsedOverride, target_kind: str) -> dict[str, Any]:
    """Session node is Overlay ``target``; the rest of the path is ``spec.patch``."""
    patch_segments = _patch_segments(override.path)
    if not patch_segments:
        raise ValueError("override path must address a field below spec")
    live: Any = document
    if override.path.segments and override.path.segments[0].name == "spec":
        live = document.get("spec") if isinstance(document.get("spec"), dict) else document
    patch = _content_patch(patch_segments, override.value, live)
    if not isinstance(patch, dict):
        raise ValueError("override path must address a field below spec")
    if target_kind == "MAS":
        patch = _lift_mas_agency_patch(patch)
    return _overlay_document(target_kind, override.path, patch)


def overlay_for_session_agent(source: str) -> dict[str, Any]:
    """CLI ``.mas.agent[id].…`` as Overlay target Agent/name and spec.patch content."""
    parsed = parse_override(source)
    if parsed.path.root != "agent":
        raise ValueError(f"nested agent override required, got {source!r}")
    patch_segments = _patch_segments(parsed.path)
    if any(segment.wildcard or isinstance(segment.selector, int) for segment in patch_segments):
        return _overlay_document("Agent", parsed.path, {}, overrides=[source])
    patch = _content_patch(patch_segments, parsed.value)
    if not isinstance(patch, dict):
        raise ValueError(f"nested agent override required, got {source!r}")
    return _overlay_document("Agent", parsed.path, patch)


def _document_roots(schema_kind: str) -> set[str]:
    hyphen = schema_kind.replace("_", "-")
    underscore = schema_kind.replace("-", "_")
    roots = {schema_kind, hyphen, underscore}
    if schema_kind in {"config", "workspace"}:
        roots.update({"config", "workspace"})
    if schema_kind in {"mas", "workflow", "app"}:
        roots.update({"mas", "workflow", "app"})
    return roots


def _unknown_document_root(root: str, schema_kind: str) -> str:
    expected = schema_kind.replace("-", "_")
    if root == "manifest":
        return (
            f"override root 'manifest' is not a session node; this file is {expected!r} "
            f"— use .{expected}.…"
        )
    return (
        f"override path does not match this document; this file is {schema_kind!r} "
        f"— use .{expected}.…"
    )


def _assert_live_selectors(document: dict[str, Any], path: OverridePath) -> None:
    """Reject selectors on scalars and identity updates that do not exist."""
    current: Any = document
    for position, segment in enumerate(path.segments):
        last = position == len(path.segments) - 1
        if current is None:
            if segment.selector is not None or segment.wildcard:
                raise ValueError(f"no {segment.name!r} entry matches {segment.selector_text}")
            return
        if not isinstance(current, dict):
            raise ValueError(
                f"path segment {segment.name!r} does not exist on a {type(current).__name__} value"
            )
        value = current.get(segment.name)
        if segment.map_key is not None:
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{segment.name!r} is not a map")
            if last:
                return
            current = value.get(segment.map_key) if isinstance(value, dict) else None
            continue
        if segment.selector is None and not segment.wildcard:
            if last:
                return
            current = value
            continue
        if value is not None and not isinstance(value, list):
            raise ValueError(f"{segment.name!r} is not a list and cannot be selected")
        if not isinstance(value, list) or not _selected_indexes(value, segment):
            if last and isinstance(segment.selector, tuple):
                return
            raise ValueError(f"no {segment.name!r} entry matches {segment.selector_text}")
        if last:
            return
        indexes = _selected_indexes(value, segment)
        current = value[indexes[0]] if len(indexes) == 1 else None


def _apply_assignment(
    document: dict[str, Any],
    override: ParsedOverride,
    *,
    target_kind: str,
    merge_spec: bool,
) -> dict[str, Any]:
    """Apply one assignment through Overlay ``spec.patch``, or RFC 7396 for envelope fields."""
    _require_standalone_target(document, override)
    assert_cli_allowed(override.path, target_kind, instance=document)
    assert_path_declared(load_target_schema(target_kind), override.path, instance=document)
    _assert_live_selectors(document, override.path)
    if _uses_op(override.value) and not _spec_overlay_path(override.path):
        raise ValueError(f"$op requires a spec overlay target: {override.source!r}")
    if _uses_op(override.value) or (merge_spec and _spec_overlay_path(override.path)):
        return merge_overlay(document, _overlay_for_override(document, override, target_kind))
    patch = _content_patch(list(override.path.segments), override.value, document)
    if not isinstance(patch, dict):
        raise ValueError(f"override path must address a field under the session node: {override.source!r}")
    return apply_identity_merge_patch(document, patch)


def overrides_for_root(
    overrides: tuple[str, ...] | list[str],
    root: str,
    *,
    parent: str | None = None,
) -> tuple[str, ...]:
    """The subset of *overrides* addressed to *root* (and optional parent), in argument order."""
    selected: list[str] = []
    for source in overrides:
        path = parse_override(source).path
        if path.root == root and path.parent == parent:
            selected.append(source)
    return tuple(selected)


def apply_cli_overrides(
    document: dict[str, Any], overrides: tuple[str, ...] | list[str], *, root: str
) -> dict[str, Any]:
    """Apply CLI assignments through overlay merge for ``spec``, or RFC 7396 for envelope fields."""
    target_kind = _ROOT_TARGETS.get(root)
    if target_kind is None:
        raise ValueError(f"CLI overrides do not support root {root!r} yet")
    result = deepcopy(document)
    for source in overrides:
        parsed = parse_override(source)
        if parsed.path.root != root:
            raise ValueError(f"override root {parsed.path.root!r} does not match command root {root!r}: {source!r}")
        result = _apply_assignment(result, parsed, target_kind=target_kind, merge_spec=True)
    return result


def apply_root_overrides(
    document: dict[str, Any], overrides: tuple[str, ...] | list[str], *, root: str
) -> dict[str, Any]:
    """Apply mixed CLI overrides that match *root*; leave other roots untouched."""
    selected = overrides_for_root(overrides, root)
    if not selected:
        return document
    return apply_cli_overrides(document, selected, root=root)


def apply_manifest_overrides(
    document: dict[str, Any],
    overrides: tuple[str, ...] | list[str],
    *,
    validate: bool = True,
    schema_kind: str | None = None,
) -> dict[str, Any]:
    """Patch any schema-backed document. The override root is that document's kind."""
    from mas.ctl.validate import validate_data
    from mas.ctl.validate.schemas import declared_kind

    declared = declared_kind(document)
    if schema_kind is not None and declared is not None and schema_kind != declared:
        raise ValueError(f"explicit schema {schema_kind!r} does not match declared kind {declared!r}")
    schema_kind = schema_kind or declared
    if schema_kind is None:
        raise ValueError("manifest overrides require a document with a registered schema")
    load_schema(schema_kind)
    overlay_kind = _ROOT_TARGETS.get(schema_kind, schema_kind)
    merge_spec = overlay_kind in _ROOT_TARGETS.values()
    result = deepcopy(document)
    for source in overrides:
        parsed = parse_override(source)
        if parsed.path.root not in _document_roots(schema_kind):
            raise ValueError(f"{_unknown_document_root(parsed.path.root, schema_kind)}: {source!r}")
        if _uses_op(parsed.value) and not merge_spec:
            raise ValueError(f"$op requires a supported spec overlay target: {source!r}")
        result = _apply_assignment(result, parsed, target_kind=overlay_kind, merge_spec=merge_spec)
        if validate:
            validate_data(
                result,
                source=f"manifest override {parsed.path.text}",
                kind=schema_kind,
            ).raise_if_failed()
    return result
