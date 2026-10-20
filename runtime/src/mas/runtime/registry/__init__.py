#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Plugin URN registry — runtime plugin types, entries, and registry façade."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VariantInfo:
    module: str
    class_name: str
    version: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)
    extra: str = ""

    def load_class(self) -> type:
        mod = importlib.import_module(self.module)
        return getattr(mod, self.class_name)

    def missing_requires(self) -> list[str]:
        """``requires`` entries that are not installed, without importing them.

        Catalog scans (:meth:`PluginRegistry.list`, ``list_steps``, plugin
        doctor) must not execute a ``requires`` module or its parents.
        ``import_module`` and ``find_spec("a.b.c")`` both do.
        """
        return [name for name in self.requires if not _requirement_discoverable(name)]


def _requirement_discoverable(dotted: str) -> bool:
    """True when *dotted* is installed, without importing any of its parts.

    Uses only ``sys.modules`` (already loaded), stdlib/builtin names, and
    filesystem lookups on ``sys.path``. No ``find_spec`` / ``import_module``.
    """
    parts = [part for part in str(dotted).split(".") if part]
    if not parts:
        return False
    top, rest = parts[0], parts[1:]
    loaded = sys.modules.get(top)
    if loaded is not None:
        if not rest:
            return True
        search = [Path(entry) for entry in (getattr(loaded, "__path__", None) or [])]
        return bool(search) and _path_has_parts(search, rest)
    if top in sys.builtin_module_names:
        return not rest
    if top in getattr(sys, "stdlib_module_names", ()):
        return not rest or _sys_path_has(top, rest)
    return _sys_path_has(top, rest)


def _sys_path_has(top: str, rest: list[str]) -> bool:
    for entry in sys.path:
        if not entry:
            continue
        root = Path(entry)
        package = root / top
        if package.is_dir():
            if not rest or _path_has_parts([package], rest):
                return True
        elif not rest and (root / f"{top}.py").is_file():
            return True
    return False


def _path_has_parts(search: list[Path], parts: list[str]) -> bool:
    *dirs, last = parts
    for base in search:
        current = base
        for name in dirs:
            current = current / name
            if not current.is_dir():
                break
        else:
            if (current / last).is_dir() or (current / f"{last}.py").is_file():
                return True
    return False


class PluginUnavailable(RuntimeError):
    """A plugin's declared ``requires`` are not importable.

    Raised by :meth:`PluginEntry.resolve` instead of letting the eventual
    ``import`` inside :meth:`VariantInfo.load_class` fail deep inside
    plugin construction — the same missing-dependency case, but caught at
    the point a caller asks for the plugin instead of wherever it happens
    to first touch the missing module.
    """

    def __init__(self, urn: str, *, missing: list[str], extra: str = "") -> None:
        self.urn = urn
        self.missing = list(missing)
        self.extra = extra
        hint = f"mas-ctl plugin enable {urn}" if extra else f"pip install {' '.join(missing)}"
        super().__init__(
            f"plugin {urn!r} is unavailable: missing {', '.join(missing)} ({hint})"
        )


@dataclass
class PluginEntry:
    urn: str
    description: str = ""
    default_variant: str = "builtin"
    shortcuts: list[str] = field(default_factory=list)
    variants: dict[str, VariantInfo] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def default(self) -> VariantInfo | None:
        return self.variants.get(self.default_variant)

    def resolve(self, variant: str = "") -> VariantInfo:
        key = variant or self.default_variant
        if key not in self.variants:
            raise ValueError(
                f"Unknown variant {key!r} for {self.urn}. "
                f"Available: {list(self.variants)}"
            )
        info = self.variants[key]
        missing = info.missing_requires()
        if missing:
            raise PluginUnavailable(self.urn, missing=missing, extra=info.extra)
        return info


def _normalise_type_name(type_name: str) -> str:
    return str(type_name).strip().lower().replace("-", "_")


def _urn_token(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(".", "_").replace("/", "_").replace("@", "_")


def _canonical_type_name(type_name: str) -> str:
    return _normalise_type_name(type_name)


def _urn_for_type_name(type_name: str, name: str) -> str:
    category = _normalise_type_name(type_name)
    token = _urn_token(name)
    return f"mas.{category}.{token}"


def _plugin_type_from_urn(urn: str) -> str:
    parts = str(urn).split(".")
    if len(parts) >= 3:
        return _normalise_type_name(parts[1])
    return ""


class PluginRegistry:
    """Runtime plugin catalog — query via manifest ``spec.<key>`` names."""

    def __init__(self) -> None:
        self._entries: dict[str, PluginEntry] = {}
        self._aliases: dict[str, str] = {}
        self._scan_paths: list[Path] = []
        self._known_types: set[str] = set()
        self._spec_defaults: dict[str, str] = {}
        self._runtime_spec_keys: set[str] = set()
        self._by_type: dict[str, list[PluginEntry]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, entry: PluginEntry) -> None:
        if not str(entry.attributes.get("plugin_type") or "").strip():
            inferred_type = _plugin_type_from_urn(entry.urn)
            if inferred_type:
                entry.attributes["plugin_type"] = inferred_type
                self.register_type(inferred_type)
        previous = self._entries.get(entry.urn)
        if previous is not None:
            old_type = self._entry_type(previous)
            bucket = self._by_type.get(old_type) if old_type else None
            if bucket:
                self._by_type[old_type] = [item for item in bucket if item.urn != entry.urn]
        self._entries[entry.urn] = entry
        plugin_type = self._entry_type(entry)
        if plugin_type:
            typed = self._by_type.setdefault(plugin_type, [])
            if all(item.urn != entry.urn for item in typed):
                typed.append(entry)
        # register_alias() can append to entry.shortcuts (for the entry
        # that owns the alias' target URN). Iterate a snapshot rather than
        # the live list so registering an alias mid-loop can't change what
        # this loop iterates over.
        for sc in list(entry.shortcuts):
            self.register_alias(sc, entry.urn)

    def unregister(self, urn: str) -> None:
        """Drop a plugin and its type-index / alias rows. Tests and live spec use this."""
        entry = self._entries.pop(urn, None)
        if entry is None:
            return
        plugin_type = self._entry_type(entry)
        bucket = self._by_type.get(plugin_type) if plugin_type else None
        if bucket:
            remaining = [item for item in bucket if item.urn != urn]
            if remaining:
                self._by_type[plugin_type] = remaining
            else:
                self._by_type.pop(plugin_type, None)
        for alias, target in list(self._aliases.items()):
            if target == urn:
                del self._aliases[alias]

    def register_type(self, plugin_type: str, *, layer: str | None = None) -> None:
        from mas.runtime.harness.catalog import UnknownBoundarySlotError, classify_plugin_type

        canonical = _canonical_type_name(plugin_type)
        inferred = classify_plugin_type(canonical)
        if layer == "boundary" and inferred != "boundary":
            raise UnknownBoundarySlotError(
                f"{plugin_type!r} is not a boundary slot. The envelope alphabet "
                "is closed; a new slot is a 15th hook. Use a harness composition."
            )
        self._known_types.add(canonical)

    def register_types(self, plugin_types: set[str]) -> None:
        for plugin_type in plugin_types:
            self.register_type(plugin_type)

    def register_runtime_spec_keys(self, spec_keys: set[str]) -> None:
        for spec_key in spec_keys:
            self._runtime_spec_keys.add(_canonical_type_name(spec_key))

    def set_default(self, spec_key: str, plugin_id: str) -> None:
        self._spec_defaults[_canonical_type_name(spec_key)] = str(plugin_id)

    def register_alias(self, alias: str, urn: str) -> None:
        key = str(alias).strip().lower()
        self._aliases[key] = urn
        entry = self._entries.get(urn)
        if entry is not None and key not in {str(sc).strip().lower() for sc in entry.shortcuts}:
            entry.shortcuts.append(alias)

    def validate_aliases(self) -> None:
        """Raise if any registered alias points at an unregistered URN.

        Alias tables (``aliases.yaml`` + workspace overrides) are hand-edited
        YAML with no other structural check tying a target back to a real
        plugin. A typo, or dropping a plugin without dropping its alias,
        previously failed silently: ``resolve()``/``get()`` just returned
        ``None`` and the caller hit a confusing "unknown plugin" error far
        from the actual mistake. Call this right after aliases are loaded
        so bad alias tables fail loudly at bootstrap time instead.
        """
        dangling = {
            alias: urn
            for alias, urn in self._aliases.items()
            if urn not in self._entries and urn.lower() not in self._aliases
        }
        if dangling:
            details = ", ".join(f"{alias!r} -> {urn!r}" for alias, urn in sorted(dangling.items()))
            raise ValueError(
                f"Alias table has {len(dangling)} alias(es) pointing at unregistered "
                f"plugin URNs: {details}"
            )

    # ── Resolution (manifest spec bindings) ──────────────────────────────

    @staticmethod
    def binding_plugin_id(binding: dict[str, Any] | None) -> str:
        """Extract ``type`` or ``ref`` from a ``spec.<key>`` object."""
        raw = binding or {}
        return str(raw.get("type") or raw.get("ref") or "")

    def default_for(self, spec_key: str) -> str:
        return self._spec_defaults.get(_canonical_type_name(spec_key), "")

    def runtime_spec_keys(self) -> frozenset[str]:
        if self._runtime_spec_keys:
            return frozenset(self._runtime_spec_keys)
        return frozenset(self._spec_defaults)

    @staticmethod
    def _entry_type(entry: PluginEntry) -> str:
        return _canonical_type_name(str(entry.attributes.get("plugin_type") or ""))

    def _entries_for_type(self, plugin_type: str) -> list[PluginEntry]:
        canonical = _canonical_type_name(plugin_type)
        return list(self._by_type.get(canonical, ()))

    @staticmethod
    def _matches_name(entry: PluginEntry, name: str) -> bool:
        key = str(name).strip().lower()
        if key == entry.urn.lower():
            return True
        if key in {str(sc).lower() for sc in entry.shortcuts}:
            return True
        key_token = key.replace("-", "_")
        return entry.urn.lower().endswith(f".{key_token}")

    def resolve_spec(
        self,
        spec_key: str,
        binding: dict[str, Any] | None = None,
    ) -> VariantInfo:
        """Resolve ``spec.<spec_key>`` binding to a :class:`VariantInfo`."""
        plugin_type = _canonical_type_name(spec_key)
        name = self.binding_plugin_id(binding) or self.default_for(spec_key)
        info = self.resolve_by_type(plugin_type, name)
        if info is None:
            raise KeyError(
                f"no plugin registered for spec.{spec_key} "
                f"type/ref={name!r}"
            )
        return info

    def instantiate_by_type(self, plugin_type: str, name: str, *args: Any, **kwargs: Any) -> Any:
        """Construct a registered plugin by type and name. No class is named here."""
        info = self.resolve_by_type(plugin_type, name)
        if info is None:
            raise KeyError(f"{plugin_type} plugin {name!r} was not found")
        return info.load_class()(*args, **kwargs)

    def create(
        self,
        spec_key: str,
        binding: dict[str, Any] | None = None,
        *,
        manifest: dict | None = None,
        **params: Any,
    ) -> Any:
        """Instantiate the plugin declared under ``spec.<spec_key>``.

        Reads the binding leniently: ``ctl`` already validates every
        singleton plugin slot at authoring time, so this is the runtime's
        one central plugin-construction path, hit by every spec key
        (``design_pattern``, ``context_manager``, ``assembler``, ``summarizer``,
        ...). A malformed value that bypassed ``ctl`` (e.g. a hand-built
        manifest in a test or embedding) degrades to the slot's default
        plugin rather than failing construction.
        """
        from mas.runtime.spec.plugin_binding import normalize_plugin_binding_lenient

        if binding is None and manifest is not None:
            raw = (manifest.get("spec") or {}).get(spec_key)
            binding = normalize_plugin_binding_lenient(raw, field=f"spec.{spec_key}")
        else:
            binding = normalize_plugin_binding_lenient(binding, field=f"spec.{spec_key}")
        info = self.resolve_spec(spec_key, binding)
        cls = info.load_class()
        ctor_params = {**dict(binding.get("params") or {}), **params}
        try:
            return cls(**ctor_params)
        except TypeError as exc:
            raise TypeError(
                f"PluginRegistry.create({spec_key!r}): {cls.__name__}(**{ctor_params!r}) failed"
            ) from exc

    def resolve(self, name: str, variant: str = "") -> VariantInfo | None:
        key = name.lower()
        if key in self._entries:
            return self._entries[key].resolve(variant)
        urn = self._aliases.get(key)
        if urn and urn in self._entries:
            return self._entries[urn].resolve(variant)
        return None

    def resolve_by_type(self, plugin_type: str, name: str) -> VariantInfo | None:
        urn = self.urn_for(name)
        if urn:
            entry = self._entries.get(urn)
            if entry and self._entry_type(entry) == _canonical_type_name(plugin_type):
                return entry.resolve()
        for entry in self._entries_for_type(plugin_type):
            if self._matches_name(entry, name):
                return entry.resolve()
        return None

    def get(
        self,
        plugin_type: str,
        name: str | None = None,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> VariantInfo | None:
        """Generic plugin query by type, optional name and optional attributes."""
        attrs = dict(attributes or {})
        if name:
            for entry in self._entries_for_type(plugin_type):
                if not self._matches_name(entry, name):
                    continue
                if any(entry.attributes.get(key) != expected for key, expected in attrs.items()):
                    continue
                return entry.resolve()
            return None

        for entry in self._entries_for_type(plugin_type):
            if all(entry.attributes.get(key) == value for key, value in attrs.items()):
                return entry.resolve()
        return None

    def urn_for(self, name: str) -> str | None:
        key = name.lower()
        if key in self._entries:
            return key
        return self._aliases.get(key)

    def get_entry(self, urn: str) -> PluginEntry | None:
        """Look up a :class:`PluginEntry` by URN or alias, without resolving a variant.

        Unlike :meth:`resolve`/:meth:`get`, never raises :class:`PluginUnavailable` —
        for callers (CLI listing, ``mas-ctl plugin enable``) that need the entry
        itself to inspect or fix availability, not to use the plugin.
        """
        key = str(urn).strip().lower()
        if key in self._entries:
            return self._entries[key]
        aliased = self._aliases.get(key)
        if aliased and aliased in self._entries:
            return self._entries[aliased]
        return None

    def list_names(self, plugin_type: str) -> list[str]:
        """List manifest-facing names for a plugin category without loading variants."""
        names: set[str] = set()
        for entry in self._entries_for_type(plugin_type):
            names.add(entry.urn.rsplit(".", 1)[-1])
            names.update(str(shortcut).strip().lower() for shortcut in entry.shortcuts if str(shortcut).strip())
            names.update(
                alias
                for alias, target in self._aliases.items()
                if target == entry.urn
            )
        return sorted(names)

    # ── Query (UI / CLI / lab delegation) ────────────────────────────────

    def all_entries(self) -> list[PluginEntry]:
        return [self._entries[u] for u in self.list_all()]

    def list(self, spec_key: str | None = None) -> list[dict[str, Any]]:
        """List plugins; *spec_key* matches agent manifest ``spec.<key>``."""
        if spec_key is None:
            return self._entries_as_dicts(self.all_entries())
        return self._entries_as_dicts(self._entries_for_type(spec_key))

    def list_all(self) -> list[str]:
        return sorted(self._entries)

    def list_categories(self) -> list[str]:
        cats: set[str] = {self._entry_type(entry) for entry in self._entries.values() if self._entry_type(entry)}
        return sorted(cats)

    def get_by_category(self, category: str) -> list[PluginEntry]:
        out = self._entries_for_type(category)
        return sorted(out, key=lambda e: e.urn)

    @staticmethod
    def _entries_as_dicts(entries: list[PluginEntry]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for entry in entries:
            default = entry.default
            missing = default.missing_requires() if default else []
            items.append(
                {
                    "urn": entry.urn,
                    "description": entry.description,
                    "shortcuts": list(entry.shortcuts),
                    "category": PluginRegistry._entry_type(entry),
                    "module": default.module if default else "",
                    "class_name": default.class_name if default else "",
                    "attributes": dict(entry.attributes),
                    "available": not missing,
                    "missing": missing,
                    "extra": default.extra if default else "",
                }
            )
        return items

    def all_aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def add_scan_path(self, path: Path | str) -> None:
        self._scan_paths.append(Path(path))


_instance: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Single runtime registry entry point.

    Process-wide singleton: safe to call from any kernel instance, but not
    safe to mutate concurrently without external locking. Design-pattern
    plugins are not cached on the registry itself — each
    :class:`RuntimeKernel` resolves and instantiates its own plugin fresh at
    construction time via ``_load_design_pattern_plugin`` (see
    ``kernel/orchestrator.py``).
    """
    global _instance
    if _instance is None:
        _instance = _bootstrap.load_registry()
    return _instance


def add_plugin_path(path: str) -> None:
    get_registry().add_scan_path(path)


def register_plugin(
    urn: str,
    cls: type,
    *,
    shortcuts: list[str] | None = None,
    variant: str = "builtin",
    description: str = "",
    attributes: dict[str, Any] | None = None,
) -> None:
    _bootstrap.register_plugin(
        get_registry(),
        urn,
        cls,
        shortcuts=shortcuts,
        variant=variant,
        description=description,
        attributes=attributes,
    )


def unregister_plugin(urn: str) -> None:
    get_registry().unregister(urn)


def register_manifest_data(manifest_data: dict[str, Any]) -> None:
    _bootstrap.register_manifest_data(get_registry(), manifest_data)


def register_manifest_file(path: str | Path) -> None:
    _bootstrap.register_manifest_file(get_registry(), path)


__all__ = [
    "PluginEntry",
    "PluginRegistry",
    "PluginUnavailable",
    "VariantInfo",
    "add_plugin_path",
    "get_registry",
    "register_manifest_data",
    "register_manifest_file",
    "register_plugin",
    "unregister_plugin",
]


from . import bootstrap as _bootstrap
