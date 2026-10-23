"""Structured representations for CLI override paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

IDENTITY_KEYS = ("id", "middleware", "name")


def identity_pair(item: Any) -> tuple[str, Any] | None:
    """Return the first overlay identity key present on a list row."""
    if not isinstance(item, dict):
        return None
    for key in IDENTITY_KEYS:
        value = item.get(key)
        if value not in (None, ""):
            return key, value
    return None


@dataclass(frozen=True)
class PathSegment:
    """One mapping key and optional list selector in an override path."""

    name: str
    selector: int | tuple[str, Any] | None = None
    wildcard: bool = False
    map_key: str | None = None

    @property
    def selector_text(self) -> str:
        """Human-readable selector, e.g. ``id='planner'`` or ``*``."""
        if self.wildcard:
            return "*"
        if self.map_key is not None:
            return repr(self.map_key)
        if self.selector is None:
            return ""
        if isinstance(self.selector, int):
            return str(self.selector)
        key, value = self.selector
        return f"{key}={value!r}"


def _render_segment(segment: PathSegment) -> str:
    value = segment.name
    if segment.wildcard:
        return f"{value}[*]"
    if segment.map_key is not None:
        return f"{value}[{segment.map_key!r}]"
    if segment.selector is None:
        return value
    if isinstance(segment.selector, int):
        return f"{value}[{segment.selector}]"
    key, selector_value = segment.selector
    if key == "id":
        return f"{value}[{selector_value}]"
    return f"{value}[{key}={selector_value!r}]"


@dataclass(frozen=True)
class OverridePath:
    """A session-tree path such as ``.mas.agent[planner].spec.tools``."""

    root: str
    segments: tuple[PathSegment, ...]
    parent: str | None = None
    target_name: str | None = None
    wildcard_target: bool = False

    @property
    def text(self) -> str:
        node = self.root
        if self.target_name:
            node = f"{node}[{self.target_name}]"
        elif self.wildcard_target:
            node = f"{node}[*]"
        if self.parent:
            node = f"{self.parent}.{node}"
        field = ".".join(_render_segment(segment) for segment in self.segments)
        return f".{node}.{field}" if field else f".{node}"


@dataclass(frozen=True)
class ParsedOverride:
    """Parsed CLI assignment with a YAML-decoded value."""

    path: OverridePath
    value: Any
    source: str
