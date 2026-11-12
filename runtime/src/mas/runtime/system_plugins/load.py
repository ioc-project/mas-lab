#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load the system plugins an agent is granted.

A plugin loads when ``spec.system_plugins`` names it, when its registry row is ``default_enabled`` and it is not
named with ``enabled: false``, or when its own ``should_load`` rule over the spec grants it. ``enabled: false``
turns off all three. ``{kind: system, name}`` entries in ``spec.tools`` are the deprecated spelling of the same
grant: they load the same plugin and warn once; the same plugin named in both places takes the
``spec.system_plugins`` entry.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mas.runtime.deprecation import warn_deprecated_once
from mas.runtime.registry import PluginEntry, PluginUnavailable, construct_plugin, get_registry
from mas.runtime.registry.provider_protocol import ManifestToolLoadError
from mas.runtime.system_plugins.contract import (
    CallContext,
    SessionContext,
    SystemPluginBase,
    UnknownSystemToolError,
)
from mas.runtime.system_plugins.legacy import LEGACY_SYSTEM_TOOL, adapt_system_tool

logger = logging.getLogger(__name__)

SYSTEM_PLUGIN = "system_plugin"


def parse_system_plugin_item(item: Any) -> tuple[str, dict[str, Any]]:
    """A name, or a one-key ``{name: settings}`` mapping."""
    message = "system plugin item is a plugin id or a one-key config object"
    if isinstance(item, str):
        name = item.strip()
        if not name:
            raise ManifestToolLoadError(message)
        return name, {}
    if isinstance(item, dict) and len(item) == 1:
        name, raw = next(iter(item.items()))
        name = str(name).strip()
        if not name:
            raise ManifestToolLoadError(message)
        if raw is None:
            return name, {}
        if not isinstance(raw, dict):
            raise ManifestToolLoadError(message)
        return name, dict(raw)
    raise ManifestToolLoadError(message)


def _entry(name: str) -> PluginEntry | None:
    """The ``system_plugin`` row *name* stands for: its URN, a shortcut or an alias, matched exactly.

    A row may list names it used to have in its ``deprecated_names`` attribute: they still resolve, with a warning.
    """
    registry = get_registry()
    hits = registry.entries_named(name, SYSTEM_PLUGIN)
    if not hits:
        hits = registry.entries_named(name, LEGACY_SYSTEM_TOOL)
        for entry in hits:
            warn_deprecated_once(
                f"system-tool-type:{entry.urn}",
                f"plugin type system_tool is deprecated ({entry.urn}): register it as a system_plugin",
            )
    if len(hits) > 1:
        raise ManifestToolLoadError(f"system plugin name {name!r} is ambiguous: {sorted(e.urn for e in hits)}")
    if not hits:
        return None
    entry = hits[0]
    key = name.strip().lower()
    if key in {str(old).lower() for old in entry.attributes.get("deprecated_names") or ()}:
        warn_deprecated_once(
            f"system-plugin-name:{key}",
            f"system plugin name {name!r} is deprecated: use {entry.urn.rsplit('.', 1)[-1]!r}",
        )
    return entry


def _known_names() -> list[str]:
    return get_registry().list_names(SYSTEM_PLUGIN)


def require_entry(name: str) -> PluginEntry:
    """The ``system_plugin`` row *name* stands for; an unknown name is a load error."""
    entry = _entry(name)
    if entry is None:
        raise ManifestToolLoadError(f"unknown system plugin(s) {[name]!r}; available: {_known_names()}")
    return entry


@dataclass
class Grant:
    """One plugin the agent named: its row, its settings and whether it is turned off."""

    entry: PluginEntry
    settings: dict[str, Any]
    enabled: bool = True


def grants(spec: Mapping[str, Any], *, host_requested: Iterable[str] = ()) -> dict[str, Grant]:
    """The plugins an agent names, by URN. Unknown names raise, so a typo in ``enabled: false`` is not silent.

    Order of precedence for one plugin: ``spec.system_plugins`` > deprecated ``{kind: system}`` entry of
    ``spec.tools`` > host request. The last entry for a plugin within one list wins.
    """
    out: dict[str, Grant] = {}

    def _put(name: str, settings: dict[str, Any]) -> None:
        entry = require_entry(name)
        params = {key: value for key, value in settings.items() if key != "enabled"}
        out[entry.urn] = Grant(entry=entry, settings=params, enabled=settings.get("enabled") is not False)

    for name in (str(n).strip() for n in host_requested):
        if name:
            _put(name, {})
    for raw in spec.get("tools") or ():
        if isinstance(raw, dict) and raw.get("kind") == "system":
            name = str(raw.get("name") or "").strip()
            if not name:
                raise ManifestToolLoadError(
                    "spec.tools {kind: system} entry needs a name; list the plugin in spec.system_plugins"
                )
            warn_deprecated_once(
                f"kind-system:{name}",
                f"spec.tools entry {{kind: system, name: {name}}} is deprecated: list {name!r} in spec.system_plugins "
                "(its params become the plugin settings)",
            )
            settings = dict(raw.get("params") or {})
            if raw.get("enabled") is False:
                settings["enabled"] = False
            _put(name, settings)
    for item in spec.get("system_plugins") or ():
        name, settings = parse_system_plugin_item(item)
        # the new key wins over the deprecated spelling and the host; within the key, the last entry wins
        _put(name, settings)
    return out


def settings_for_capability(spec: Mapping[str, Any] | None, capability: str) -> dict[str, Any] | None:
    """Settings of a granted system plugin whose registry row has this ``capability``, or ``None``.

    An unknown or malformed grant raises :class:`ManifestToolLoadError`, the same as ``load_system_plugins``.
    """
    named = grants(dict(spec or {}))
    for grant in named.values():
        if str(grant.entry.attributes.get("capability") or "") != capability:
            continue
        return dict(grant.settings) if grant.enabled else None
    return None


@dataclass
class _Bound:
    name: str
    urn: str
    plugin: SystemPluginBase
    state: object
    settings: dict[str, Any]
    capability: str = ""


@dataclass
class BoundSystemPlugins:
    """Session-scoped system plugins and the tools they expose."""

    _bound: list[_Bound] = field(default_factory=list)
    _by_tool: dict[str, _Bound] = field(default_factory=dict)

    def add(self, bound: _Bound) -> None:
        self._bound.append(bound)
        for definition in bound.plugin.tools():
            if not definition.name:
                continue
            other = self._by_tool.get(definition.name)
            if other is not None and other.urn != bound.urn:
                raise ManifestToolLoadError(
                    f"system plugins {other.name!r} and {bound.name!r} both expose tool {definition.name!r}"
                )
            self._by_tool[definition.name] = bound

    def tool_names(self) -> frozenset[str]:
        return frozenset(self._by_tool)

    def advertise(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for bound in self._bound:
            for definition in bound.plugin.tools(ctx=ctx):
                if not definition.name or definition.name in seen:
                    continue
                seen.add(definition.name)
                out.append(definition.as_advertise())
        return out

    def _owner(self, tool_name: str) -> _Bound | None:
        owner = self._by_tool.get(tool_name)
        if owner is not None:
            return owner
        return next((row for row in self._bound if row.plugin.claims(tool_name, state=row.state)), None)

    def claims(self, tool_name: str) -> bool:
        return self._owner(tool_name) is not None

    def call(
        self,
        tool: str,
        args: Mapping[str, Any] | None = None,
        *,
        context: CallContext | None = None,
    ) -> Any:
        bound = self._owner(tool)
        if bound is None:
            raise UnknownSystemToolError(tool)
        return bound.plugin.call(tool, dict(args or {}), state=bound.state, context=context or CallContext())

    async def acall(
        self,
        tool: str,
        args: Mapping[str, Any] | None = None,
        *,
        context: CallContext | None = None,
    ) -> Any:
        bound = self._owner(tool)
        if bound is None:
            raise UnknownSystemToolError(tool)
        return await bound.plugin.acall(tool, dict(args or {}), state=bound.state, context=context or CallContext())

    def snapshot(self) -> dict[str, Any]:
        return {bound.urn: dict(bound.plugin.snapshot(bound.state)) for bound in self._bound}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        for bound in self._bound:
            blob = snapshot.get(bound.urn)
            if isinstance(blob, Mapping):
                bound.state = bound.plugin.restore(blob)

    def close(self) -> None:
        for bound in self._bound:
            bound.plugin.close(bound.state)

    def state_for_capability(self, capability: str) -> object | None:
        """Bind state of the granted plugin whose registry row has this ``capability``."""
        return next((row.state for row in self._bound if row.capability == capability), None)

    def __iter__(self):
        return iter(self._bound)


def _plugin_class(entry: PluginEntry) -> type[SystemPluginBase]:
    cls = entry.resolve().load_class()
    if any(entry.urn == legacy.urn for legacy in get_registry().get_by_category(LEGACY_SYSTEM_TOOL)):
        communication = str(entry.attributes.get("communication") or "").strip() or None
        return adapt_system_tool(cls, entry.urn.rsplit(".", 1)[-1], communication=communication)
    if not (isinstance(cls, type) and issubclass(cls, SystemPluginBase)):
        raise ManifestToolLoadError(f"{entry.urn!r} is not a system plugin: derive from SystemPluginBase")
    return cls


def _construct(entry: PluginEntry, settings: dict[str, Any]) -> _Bound:
    plugin = construct_plugin(_plugin_class(entry), settings)
    return _Bound(
        name=entry.urn.rsplit(".", 1)[-1],
        urn=entry.urn,
        plugin=plugin,
        state=None,
        settings=dict(settings),
        capability=str(entry.attributes.get("capability") or ""),
    )


def _implied(entry: PluginEntry, spec: Mapping[str, Any], session: SessionContext) -> bool:
    """The plugin's own rule over the spec. An optional plugin that is not installed grants nothing."""
    try:
        cls = _plugin_class(entry)
    except (PluginUnavailable, ImportError):
        return False
    return bool(cls.should_load(spec, {}, session=session))


def load_system_plugins(
    spec: Mapping[str, Any] | None = None,
    *,
    host_requested: Iterable[str] = (),
    session: SessionContext | None = None,
) -> BoundSystemPlugins:
    """Resolve, construct and bind the system plugins this agent is granted."""
    spec = dict(spec or {})
    session = session or SessionContext(spec=spec)
    named = grants(spec, host_requested=host_requested)

    to_load: list[tuple[PluginEntry, dict[str, Any]]] = [
        (grant.entry, grant.settings) for grant in named.values() if grant.enabled
    ]
    registry = get_registry()
    for entry in (*registry.get_by_category(SYSTEM_PLUGIN), *registry.get_by_category(LEGACY_SYSTEM_TOOL)):
        grant = named.get(entry.urn)
        if grant is not None:
            continue
        if entry.attributes.get("default_enabled") is True or _implied(entry, spec, session):
            to_load.append((entry, {}))

    bound = BoundSystemPlugins()
    for entry, settings in to_load:
        row = _construct(entry, settings)
        row.state = row.plugin.bind(session)
        bound.add(row)
    return bound
