"""Parser for session-tree, jq-like CLI override assignments."""

from __future__ import annotations

import re
from typing import Any

import yaml
from mas.ctl.overrides.model import OverridePath, ParsedOverride, PathSegment

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_CONFIG_ALIASES = {"config": "workspace"}
_MAS_ALIASES = {"app": "mas", "workflow": "mas"}
_ENVELOPE_FIRST = frozenset({"spec", "metadata", "apiversion", "kind", "status"})
_AGENT_CHILD = "agent"


def _split_assignment(source: str) -> tuple[str, str]:
    quote: str | None = None
    bracket_depth = 0
    for index, character in enumerate(source):
        if quote:
            if character == quote and (index == 0 or source[index - 1] != "\\"):
                quote = None
            continue
        if character in "'\"":
            quote = character
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth -= 1
        elif character == "=" and bracket_depth == 0:
            return source[:index], source[index + 1 :]
    raise ValueError(f"override must be PATH=VALUE, got {source!r}")


def _parse_selector(raw: str, source: str) -> tuple[int | tuple[str, Any] | None, bool, str | None]:
    value = raw.strip()
    if value == "*":
        return None, True, None
    if value.startswith(('"', "'")) and value.endswith(value[0]):
        map_key = yaml.safe_load(value)
        if not isinstance(map_key, str):
            raise ValueError(f"map selector must be a string in override {source!r}")
        return None, False, map_key
    if value.isdigit():
        return int(value), False, None
    if "=" not in value:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid list selector {raw!r} in override {source!r}")
        return ("id", value), False, None
    key, encoded = value.split("=", 1)
    key = key.strip()
    if not _IDENTIFIER.fullmatch(key):
        raise ValueError(f"invalid selector key {key!r} in override {source!r}")
    try:
        selector_value = yaml.safe_load(encoded)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid selector value {encoded!r} in override {source!r}") from error
    return (key, selector_value), False, None


def _split_path_segments(path: str) -> list[str]:
    segments: list[str] = []
    start = 0
    bracket_depth = 0
    quote: str | None = None
    for index, character in enumerate(path):
        if quote:
            if character == quote and path[index - 1] != "\\":
                quote = None
            continue
        if character in "'\"" and bracket_depth:
            quote = character
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth -= 1
        elif character == "." and bracket_depth == 0:
            segments.append(path[start:index])
            start = index + 1
    segments.append(path[start:])
    return segments


def _normalize_colon(raw: str) -> str:
    """``kind:path`` is an alias of ``kind.path`` for the first session node."""
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(.+)$", raw.strip())
    if not match:
        return raw.strip()
    kind = match.group(1)
    if kind.lower() in _ENVELOPE_FIRST:
        return raw.strip()
    return f"{kind}.{match.group(2)}"


def _parse_segment(raw_segment: str, source: str) -> PathSegment:
    if not raw_segment:
        raise ValueError(f"empty path segment in override {source!r}")
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(.*)\])?", raw_segment)
    if not match:
        raise ValueError(f"invalid path segment {raw_segment!r} in override {source!r}")
    name, selector_raw = match.groups()
    selector: int | tuple[str, Any] | None = None
    wildcard = False
    map_key: str | None = None
    if selector_raw is not None:
        selector, wildcard, map_key = _parse_selector(selector_raw, source)
    return PathSegment(name=name, selector=selector, wildcard=wildcard, map_key=map_key)


def _canonical_node(name: str) -> str:
    lowered = name.lower()
    return _CONFIG_ALIASES.get(lowered, _MAS_ALIASES.get(lowered, lowered))


def _target_name(segment: PathSegment) -> str | None:
    if segment.map_key is not None:
        return segment.map_key
    if isinstance(segment.selector, tuple):
        _key, expected = segment.selector
        return None if expected is None else str(expected)
    return None


def _refuse_agent_index(segment: PathSegment) -> None:
    if isinstance(segment.selector, int):
        raise ValueError(".mas.agent needs a name or id (.mas.agent[planner]), not an index")


def _agent_segment(
    node: PathSegment, rest: list[PathSegment]
) -> tuple[PathSegment, list[PathSegment], str | None] | None:
    """The agent segment of a session path, or None when the path does not target an agent."""
    root = _canonical_node(node.name)
    if rest and rest[0].name.lower() == _AGENT_CHILD and root == "mas":
        return rest[0], rest[1:], "mas"
    if root == "agent":
        return node, rest, None
    return None


def _parse_path(raw: str, source: str) -> OverridePath:
    text = _normalize_colon(raw.strip().lstrip("."))
    parsed = [_parse_segment(part, source) for part in _split_path_segments(text)]
    if not parsed:
        raise ValueError(
            f"override path must start at the session tree (.mas.spec…, .infra.spec…, .agent.spec…), got {raw!r}"
        )
    node = parsed[0]
    if node.name.lower() == "root":
        raise ValueError(f"override path starts at the session; use .agent.spec… not root.agent.spec…, got {raw!r}")
    if node.name.lower() in _ENVELOPE_FIRST:
        raise ValueError(
            f"override path must start with a session node (.mas, .infra, .config, .flavour, .agent), got {raw!r}"
        )
    rest = parsed[1:]
    parent: str | None = None
    root = _canonical_node(node.name)
    target_name = _target_name(node)
    wildcard_target = node.wildcard
    if isinstance(node.selector, int):
        raise ValueError(
            f"session node {node.name!r} needs a name or id "
            f"({node.name}[planner] or {node.name}[id=planner]), not an index"
        )
    peeled = _agent_segment(node, rest)
    if peeled is not None:
        child, peeled_rest, parent = peeled
        if parent == "mas":
            _refuse_agent_index(child)
            root = "agent"
            target_name = _target_name(child)
            wildcard_target = child.wildcard
            rest = peeled_rest
    if not rest:
        raise ValueError(f"override path must address a field under the session node, got {raw!r}")
    return OverridePath(
        root=root,
        segments=tuple(rest),
        parent=parent,
        target_name=target_name,
        wildcard_target=wildcard_target,
    )


def parse_agent_target(source: str) -> tuple[bool, str | None]:
    """Whether *source* names every agent or one agent id.

    Same agent targeting as ``--override`` without a field: ``.mas.agent[id]``,
    ``.mas.agent[id=name]``, ``.mas.agent[*]``, ``.agent[id]``, ``mas:agent[id]``.
    A field after the agent (``.mas.agent[id].spec``) is not a Control selector.
    """
    text = _normalize_colon(source.strip().lstrip("."))
    if not text:
        raise ValueError(f"empty agent path {source!r}")
    parts = [_parse_segment(part, source) for part in _split_path_segments(text)]
    node = parts[0]
    if node.name.lower() in _ENVELOPE_FIRST or node.name.lower() == "root":
        raise ValueError(f"{source!r} is not an agent path; use .mas.agent[id] or .mas.agent[*]")
    peeled = _agent_segment(node, parts[1:])
    if peeled is None:
        raise ValueError(f"{source!r} is not an agent path; use .mas.agent[id] or .mas.agent[*]")
    agent_seg, rest, _parent = peeled
    if rest:
        raise ValueError(f"{source!r} addresses a field; a Control selector is the agent only")
    _refuse_agent_index(agent_seg)
    if agent_seg.wildcard:
        return True, None
    name = _target_name(agent_seg)
    if not name:
        raise ValueError(f"{source!r} needs a name or id (.mas.agent[planner]) or *")
    return False, str(name)


def parse_override(source: str) -> ParsedOverride:
    """Parse ``.mas.agent[id].spec.field=value`` (or ``kind:path=value``) as YAML."""
    raw_path, raw_value = _split_assignment(source)
    path = _parse_path(raw_path.strip(), source)
    try:
        value = "" if raw_value == "" else yaml.safe_load(raw_value)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML value {raw_value!r} in override {source!r}") from error
    return ParsedOverride(path=path, value=value, source=source)
