"""Walk a JSON Schema along an override path.

``oneOf`` / ``anyOf`` union alternatives so every documented field is
reachable. ``allOf`` and matching ``if``/``then`` intersect: a closed
plugin-params schema wins over an open default. Without an instance,
``if``/``then`` branches are unioned so plugin fields stay discoverable.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from jsonschema import Draft7Validator
from mas.ctl.overrides.model import OverridePath, PathSegment, identity_pair
from mas.ctl.validate.schemas import load_schema

_OPEN_MAP: dict[str, Any] = {"additionalProperties": True}


def load_target_schema(target_kind: str) -> dict[str, Any] | None:
    """Return the schema for *target_kind*, or ``None`` when the document is unconstrained."""
    if target_kind.lower() == "workspace":
        return None
    return load_schema(target_kind)


def path_is_declared(schema: dict[str, Any], path: OverridePath, instance: Any | None = None) -> bool:
    """True when every named segment of *path* is documented by *schema*."""
    return bool(_nodes_at(schema, path, instance, root=schema))


def path_may_create(target_kind: str, path: OverridePath, instance: Any | None = None) -> bool:
    """True when missing keys along *path* may be created for *target_kind*."""
    schema = load_target_schema(target_kind)
    if schema is None:
        return True
    return path_is_declared(schema, path, instance)


def assert_path_declared(
    schema: dict[str, Any] | None,
    path: OverridePath,
    instance: Any | None = None,
) -> None:
    """Raise when a named segment is not in *schema* (given *instance*)."""
    if schema is None:
        return
    if path_is_declared(schema, path, instance):
        return
    raise ValueError(f"override path {path.text!r} is not in the schema")


def assert_patch_content(
    schema: dict[str, Any] | None,
    patch: dict[str, Any],
    *,
    root: str,
    instance: Any | None = None,
) -> None:
    """Walk Overlay ``spec.patch`` the same way as a CLI path (levels 2+)."""
    if schema is None or not isinstance(patch, dict):
        return
    for key, value in patch.items():
        if str(key).startswith("x-"):
            continue
        _walk_patch_field(
            schema,
            value,
            instance,
            root,
            (PathSegment("spec"), PathSegment(str(key))),
        )


def _is_op_value(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("$op"), dict)


def _walk_patch_field(
    schema: dict[str, Any],
    value: Any,
    instance: Any | None,
    root: str,
    segments: tuple[PathSegment, ...],
) -> None:
    assert_path_declared(schema, OverridePath(root=root, segments=segments), instance)
    if _is_op_value(value) or not isinstance(value, (dict, list)):
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).startswith("x-"):
                continue
            _walk_patch_field(
                schema,
                child,
                instance,
                root,
                (*segments, PathSegment(str(key))),
            )
        return
    field = segments[-1]
    for item in value:
        if not isinstance(item, dict):
            continue
        ident = identity_pair(item)
        # Plugin bindings are one-key maps (`{native: {path: …}}`), not
        # identity rows. Enter the array-item schema via wildcard.
        item_seg = (
            PathSegment(field.name, selector=ident)
            if ident is not None
            else PathSegment(field.name, wildcard=True)
        )
        prefix = (*segments[:-1], item_seg)
        for key, child in item.items():
            if ident is not None and key == ident[0]:
                continue
            _walk_patch_field(schema, child, instance, root, (*prefix, PathSegment(str(key))))


def assert_cli_allowed(path: OverridePath, target_kind: str, instance: Any | None = None) -> None:
    """Raise when a documented node on *path* sets ``x-cli.allowed: false``."""
    schema = load_target_schema(target_kind)
    if schema is None:
        return
    node: dict[str, Any] | None = schema
    current = instance
    for segment in path.segments:
        if node is None:
            return
        child = _child_schema(node, segment.name, current, schema)
        if child is None:
            return
        if _cli_denied(child):
            raise ValueError(f"CLI override is not allowed for path {path.text!r}")
        node = child
        if segment.selector is not None or segment.wildcard:
            node = _array_item_schema(node, schema) or node
        if segment.map_key is not None:
            node = _map_child_schema(node, segment.map_key, schema)
        current = _instance_child(current, segment)


def _cli_denied(node: dict[str, Any]) -> bool:
    for candidate in _flatten(node):
        cli_meta = candidate.get("x-cli") if isinstance(candidate, dict) else None
        if isinstance(cli_meta, dict) and cli_meta.get("allowed") is False:
            return True
    return False


def _nodes_at(
    schema: dict[str, Any],
    path: OverridePath,
    instance: Any | None = None,
    *,
    root: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    node: dict[str, Any] | None = schema
    current = instance
    root = root or schema
    for segment in path.segments:
        if node is None:
            return ()
        child = _child_schema(node, segment.name, current, root)
        if child is None:
            return ()
        if segment.selector is not None or segment.wildcard:
            child = _array_item_schema(child, root)
            if child is None:
                return ()
        if segment.map_key is not None:
            child = _map_child_schema(child, segment.map_key, root)
            if child is None:
                return ()
        node = child
        current = _instance_child(current, segment)
    return (node,) if node is not None else ()


def _deref(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    current: Any = node
    while isinstance(current, dict) and isinstance(current.get("$ref"), str):
        ref = current["$ref"]
        if not ref.startswith("#") or ref in seen:
            break
        seen.add(ref)
        pointer = ref[1:]
        if pointer in {"", "/"}:
            current = root
            continue
        target: Any = root
        for segment in pointer.strip("/").split("/"):
            segment = segment.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or segment not in target:
                return current
            target = target[segment]
        current = target
    return current if isinstance(current, dict) else node


def _with_if_then(node: dict[str, Any], instance: Any | None) -> dict[str, Any]:
    if "if" not in node:
        return node
    then = node.get("then") if isinstance(node.get("then"), dict) else None
    otherwise = node.get("else") if isinstance(node.get("else"), dict) else None
    rest = {key: value for key, value in node.items() if key not in {"if", "then", "else"}}
    if instance is None:
        return node
    extra = then if _if_matches(node["if"], instance) else otherwise
    if extra is None:
        return rest
    return {"allOf": [rest, extra]} if rest else extra


def _if_matches(if_schema: Any, instance: Any) -> bool:
    if not isinstance(if_schema, dict):
        return False
    try:
        return Draft7Validator(if_schema).is_valid(instance)
    except Exception:
        return False


def _child_schema(
    node: dict[str, Any], name: str, instance: Any | None, root: dict[str, Any]
) -> dict[str, Any] | None:
    node = _with_if_then(_deref(node, root), instance)
    if instance is None and "if" in node:
        return _union_if_then_child(node, name, root)
    if "allOf" in node:
        return _all_of_child(node, name, instance, root)
    if "oneOf" in node or "anyOf" in node:
        return _any_of_child(node, name, instance, root)
    return _own_property(node, name)


def _union_if_then_child(node: dict[str, Any], name: str, root: dict[str, Any]) -> dict[str, Any] | None:
    then = node.get("then") if isinstance(node.get("then"), dict) else None
    otherwise = node.get("else") if isinstance(node.get("else"), dict) else None
    rest = {key: value for key, value in node.items() if key not in {"if", "then", "else"}}
    allowed: list[dict[str, Any]] = []
    for part in (rest, then, otherwise):
        if not part:
            continue
        child = _child_schema(part, name, None, root)
        if child is not None:
            allowed.append(child)
    if not allowed:
        return None
    return allowed[0] if len(allowed) == 1 else {"anyOf": allowed}


def _all_of_child(
    node: dict[str, Any], name: str, instance: Any | None, root: dict[str, Any]
) -> dict[str, Any] | None:
    children: list[dict[str, Any]] = []
    rest = {key: value for key, value in node.items() if key != "allOf"}
    if rest:
        if instance is not None and _forbids(rest, name, instance, root):
            return None
        own = _child_schema(rest, name, instance, root)
        if own is not None:
            children.append(own)
    for part in node.get("allOf") or []:
        if not isinstance(part, dict):
            continue
        if instance is not None and _forbids(part, name, instance, root):
            return None
        child = _child_schema(part, name, instance, root)
        if child is not None:
            children.append(child)
    if not children:
        return None
    if instance is None:
        return children[0] if len(children) == 1 else {"anyOf": children}
    return children[0] if len(children) == 1 else {"allOf": children}


def _any_of_child(
    node: dict[str, Any], name: str, instance: Any | None, root: dict[str, Any]
) -> dict[str, Any] | None:
    allowed: list[dict[str, Any]] = []
    key = "oneOf" if "oneOf" in node else "anyOf"
    rest = {name_: value for name_, value in node.items() if name_ != key}
    if rest:
        own = _child_schema(rest, name, instance, root)
        if own is not None:
            allowed.append(own)
    for alternative in node.get(key) or []:
        if alternative is True:
            allowed.append(dict(_OPEN_MAP))
            continue
        if not isinstance(alternative, dict):
            continue
        child = _child_schema(alternative, name, instance, root)
        if child is not None:
            allowed.append(child)
    if not allowed:
        return None
    return allowed[0] if len(allowed) == 1 else {"anyOf": allowed}


def _is_open_map(node: dict[str, Any]) -> bool:
    if node == _OPEN_MAP:
        return True
    if node.get("additionalProperties") is True and not node.get("properties") and not node.get("patternProperties"):
        return True
    return (
        _is_object_schema(node)
        and not node.get("properties")
        and not node.get("patternProperties")
        and node.get("additionalProperties") is None
        and "items" not in node
    )


def _own_property(node: dict[str, Any], name: str) -> dict[str, Any] | None:
    if _is_open_map(node):
        return dict(_OPEN_MAP)
    if not _is_object_schema(node):
        return None
    properties = node.get("properties")
    if isinstance(properties, dict) and name in properties:
        return _as_schema(properties[name])
    patterns = node.get("patternProperties")
    if isinstance(patterns, dict):
        found = [_as_schema(child) for pattern, child in patterns.items() if _pattern_matches(pattern, name)]
        found = [item for item in found if item is not None]
        if found:
            return found[0] if len(found) == 1 else {"anyOf": found}
    additional = node.get("additionalProperties")
    if additional is True:
        return dict(_OPEN_MAP)
    if isinstance(additional, dict):
        return additional
    if additional is False:
        return None
    if isinstance(properties, dict) and properties:
        return None
    return dict(_OPEN_MAP)


def _forbids(node: dict[str, Any], name: str, instance: Any | None, root: dict[str, Any]) -> bool:
    node = _with_if_then(_deref(node, root), instance)
    if _own_property(node, name) is not None:
        return False
    if node.get("additionalProperties") is False:
        return True
    parts = node.get("allOf")
    if isinstance(parts, list) and any(
        isinstance(part, dict) and _forbids(part, name, instance, root) for part in parts
    ):
        return True
    alternatives = node.get("oneOf") or node.get("anyOf")
    dict_alts = [part for part in alternatives or [] if isinstance(part, dict)]
    return bool(dict_alts) and all(_forbids(part, name, instance, root) for part in dict_alts)


def _as_schema(child: Any) -> dict[str, Any] | None:
    if child is True:
        return dict(_OPEN_MAP)
    if child is False:
        return None
    if isinstance(child, dict):
        return child
    return None


def _is_object_schema(node: dict[str, Any]) -> bool:
    declared_type = node.get("type")
    if declared_type == "array":
        return False
    if declared_type == "object":
        return True
    if isinstance(declared_type, list) and "object" in declared_type:
        return True
    return any(key in node for key in ("properties", "additionalProperties", "patternProperties"))


def _pattern_matches(pattern: str, name: str) -> bool:
    try:
        return re.search(pattern, name) is not None
    except re.error:
        return False


def _flatten(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for key in ("allOf", "oneOf", "anyOf"):
        parts = node.get(key)
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    yield from _flatten(part)


def _item_schemas(node: dict[str, Any]) -> list[dict[str, Any]]:
    items = node.get("items")
    if items is True:
        return [dict(_OPEN_MAP)]
    if isinstance(items, dict):
        return [items]
    if isinstance(items, list):
        found = [item for item in items if isinstance(item, dict)]
        additional = node.get("additionalItems")
        if isinstance(additional, dict):
            found.append(additional)
        elif additional is True:
            found.append(dict(_OPEN_MAP))
        return found
    additional = node.get("additionalItems")
    if isinstance(additional, dict):
        return [additional]
    if additional is True:
        return [dict(_OPEN_MAP)]
    return []


def _array_item_schema(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any] | None:
    node = _deref(node, root)
    found = _item_schemas(node)
    if found:
        return found[0] if len(found) == 1 else {"anyOf": found}
    if _is_open_map(node) or node.get("additionalProperties") is True:
        return dict(_OPEN_MAP)
    parts: list[dict[str, Any]] = []
    combiner = "allOf" if isinstance(node.get("allOf"), list) else "anyOf"
    for key in ("allOf", "oneOf", "anyOf"):
        for part in node.get(key) or []:
            if isinstance(part, dict):
                child = _array_item_schema(part, root)
                if child is not None:
                    parts.append(child)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {combiner: parts}


def _map_child_schema(node: dict[str, Any], map_key: str, root: dict[str, Any]) -> dict[str, Any] | None:
    node = _deref(node, root)
    properties = node.get("properties")
    if isinstance(properties, dict) and map_key in properties:
        return _as_schema(properties[map_key])
    additional = node.get("additionalProperties")
    if additional is True:
        return dict(_OPEN_MAP)
    if isinstance(additional, dict):
        return additional
    parts: list[dict[str, Any]] = []
    for key in ("allOf", "oneOf", "anyOf"):
        for part in node.get(key) or []:
            if isinstance(part, dict):
                child = _map_child_schema(part, map_key, root)
                if child is not None:
                    parts.append(child)
    if not parts:
        return None
    combiner = "allOf" if isinstance(node.get("allOf"), list) else "anyOf"
    return parts[0] if len(parts) == 1 else {combiner: parts}


def _instance_child(current: Any, segment: PathSegment) -> Any:
    if not isinstance(current, dict):
        return None
    value = current.get(segment.name)
    if segment.map_key is not None:
        return value.get(segment.map_key) if isinstance(value, dict) else None
    if segment.selector is None and not segment.wildcard:
        return value
    if not isinstance(value, list):
        return None
    if segment.wildcard:
        return None
    if isinstance(segment.selector, int):
        if 0 <= segment.selector < len(value):
            return value[segment.selector]
        return None
    key, expected = segment.selector
    matches = [
        item for item in value if isinstance(item, dict) and item.get(key) == expected
    ]
    return matches[0] if len(matches) == 1 else None
