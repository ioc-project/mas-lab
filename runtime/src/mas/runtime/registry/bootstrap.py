#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest bootstrap for the runtime plugin registry."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mas.runtime.workspace_config import RuntimeWorkspaceConfig

from . import (
    PluginEntry,
    PluginRegistry,
    VariantInfo,
    _canonical_type_name,
    _normalise_type_name,
    _plugin_type_from_urn,
    _urn_for_type_name,
)
from .aliases import load_aliases, load_deprecated_aliases
from .defaults import load_defaults

logger = logging.getLogger(__name__)


@dataclass
class _ManifestPluginCandidate:
    plugin_type: str
    urn: str
    default_variant: str
    variants: dict[str, VariantInfo]
    shortcuts: list[str] = field(default_factory=list)
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    provides_types: set[str] = field(default_factory=set)


def _variant_info_from_data(vdata: dict[str, Any], *, source: Path | None = None) -> VariantInfo:
    from mas._dist import expand_extra

    module = str(vdata.get("module") or "")
    extra = expand_extra(str(vdata.get("extra") or ""), module=module or None, file=source)
    return VariantInfo(
        module=module,
        class_name=str(vdata.get("class") or vdata.get("class_name") or ""),
        version=str(vdata.get("version") or ""),
        description=str(vdata.get("description") or ""),
        requires=[str(r) for r in (vdata.get("requires") or [])],
        extra=extra,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _parse_generic_manifest(
    data: dict[str, Any],
    *,
    source: Path | None = None,
) -> tuple[
    set[str],
    list[_ManifestPluginCandidate],
    dict[str, str],
    dict[str, str],
    set[str],
]:
    known_types: set[str] = set()
    candidates: list[_ManifestPluginCandidate] = []
    role_aliases: dict[str, str] = {}
    defaults: dict[str, str] = {}
    runtime_spec_keys: set[str] = set()

    for type_name in data.get("types") or []:
        known_types.add(_canonical_type_name(str(type_name)))

    raw_plugins = data.get("plugins")
    if raw_plugins is None:
        raw_plugins = []
    elif not isinstance(raw_plugins, list):
        raise ValueError(
            "plugins: must be a list of plugin declarations (each with "
            "type:), not a mapping keyed by type. "
            f"Got {type(raw_plugins).__name__}."
        )
    for item in raw_plugins:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_manifest_item(item, source=source)
        if candidate is not None:
            # NOTE: deliberately NOT adding candidate.plugin_type to
            # known_types here. A candidate's own declared `type:` must be
            # gated through the fixpoint like everything else — it becomes
            # registerable only once it's a builtin type, listed in this
            # manifest's own `types:`, or unlocked by another candidate's
            # `provides_types`. Auto-whitelisting a candidate's type just
            # because it showed up in `plugins:` would make `provides_types`
            # meaningless (every type would already be "known" before the
            # fixpoint even starts) and would silently accept a typo'd
            # `type:` as a brand new category instead of catching it.
            candidates.append(candidate)

    aliases = data.get("aliases") or data.get("role_aliases") or {}
    if isinstance(aliases, dict):
        for role, urn in aliases.items():
            role_aliases[str(role)] = str(urn)

    manifest_defaults = data.get("defaults") or {}
    if isinstance(manifest_defaults, dict):
        for spec_key, plugin_id in manifest_defaults.items():
            defaults[_canonical_type_name(str(spec_key))] = str(plugin_id)

    for spec_key in data.get("runtime_spec_keys") or []:
        runtime_spec_keys.add(_canonical_type_name(str(spec_key)))

    return known_types, candidates, role_aliases, defaults, runtime_spec_keys


def _candidate_from_manifest_item(
    item: dict[str, Any], *, source: Path | None = None
) -> _ManifestPluginCandidate | None:
    plugin_type = str(item.get("type") or "").strip()
    if not plugin_type:
        return None

    name = str(item.get("name") or "").strip()
    urn = str(item.get("urn") or "").strip() or _urn_for_type_name(plugin_type, name)

    attributes = dict(item.get("attributes") or {})
    factory = str(item.get("factory") or attributes.get("factory") or "").strip()
    if factory:
        attributes.setdefault("factory", factory)
    provider = item.get("provider")
    if provider:
        attributes.setdefault("provider", provider)

    variants: dict[str, VariantInfo] = {}
    raw_variants = item.get("variants") or {}
    if isinstance(raw_variants, dict) and raw_variants:
        for variant_name, variant_data in raw_variants.items():
            if isinstance(variant_data, dict):
                variants[str(variant_name)] = _variant_info_from_data(variant_data, source=source)
    else:
        module = item.get("module")
        class_name = item.get("class") or item.get("class_name")
        if module and class_name:
            variants["builtin"] = _variant_info_from_data(item, source=source)
        elif factory:
            if ":" in factory:
                factory_module, factory_attr = factory.rsplit(":", 1)
            else:
                factory_module, factory_attr = factory, "build_metrics"
            from mas._dist import expand_extra

            variants["builtin"] = VariantInfo(
                module=factory_module,
                class_name=factory_attr,
                description=str(item.get("description") or ""),
                requires=[str(r) for r in (item.get("requires") or [])],
                extra=expand_extra(
                    str(item.get("extra") or ""),
                    module=factory_module or None,
                    file=source,
                ),
            )
    if not variants:
        return None

    return _ManifestPluginCandidate(
        plugin_type=plugin_type,
        urn=urn,
        default_variant=str(item.get("default") or "builtin"),
        variants=variants,
        shortcuts=[str(s) for s in (item.get("shortcuts") or ([name] if name else []))],
        description=str(item.get("description") or ""),
        attributes=attributes,
        provides_types={_canonical_type_name(str(t)) for t in (item.get("provides_types") or [])},
    )


def _register_candidates_fixpoint(
    reg: PluginRegistry,
    known_types: set[str],
    candidates: list[_ManifestPluginCandidate],
) -> None:
    pending = list(candidates)
    while pending:
        next_pending: list[_ManifestPluginCandidate] = []
        progressed = False
        for candidate in pending:
            canonical = _canonical_type_name(candidate.plugin_type)
            if canonical not in known_types:
                next_pending.append(candidate)
                continue
            reg.register_type(canonical)
            reg.register(
                PluginEntry(
                    urn=candidate.urn,
                    description=candidate.description,
                    default_variant=candidate.default_variant,
                    shortcuts=list(candidate.shortcuts),
                    variants=dict(candidate.variants),
                    attributes={"plugin_type": canonical, **dict(candidate.attributes)},
                )
            )
            known_types.update(candidate.provides_types)
            for provided in candidate.provides_types:
                reg.register_type(provided)
            progressed = True
        if not progressed:
            unresolved = sorted(f"{candidate.urn} (type={candidate.plugin_type!r})" for candidate in next_pending)
            raise ValueError(
                "Unresolved plugin manifest entries after fixpoint — the following "
                "plugin types never became known (typo'd `type:`/`provides_types:` "
                f"field, or a missing manifest entry?): {'; '.join(unresolved)}"
            )
        pending = next_pending


def load_registry(config: RuntimeWorkspaceConfig | None = None) -> PluginRegistry:
    reg = PluginRegistry()

    # "model" is a default LLM model id, not a registry plugin type/spec_key
    # -- exposed separately via mas.runtime.agent_defaults.default_model().
    for spec_key, plugin_id in load_defaults(config).items():
        if spec_key == "model":
            continue
        reg.set_default(spec_key, plugin_id)

    # Discover and register all plugins from libraries (library.yaml manifests)
    # NO hardcoded plugins in bootstrap: all plugins come from libraries!
    _register_library_plugins(reg)

    from mas.runtime.registry.llm_provider_registry import enforce_llm_provider_async_contract

    enforce_llm_provider_async_contract(reg)

    # Register aliases AFTER plugins are discovered (so URNs exist)
    for alias, urn in load_aliases(config).items():
        reg.register_alias(alias, urn)
    for alias, urn in load_deprecated_aliases().items():
        if alias.lower() not in reg._aliases:
            reg.register_alias(alias, urn, deprecated=True)
    reg.validate_aliases()

    return reg


def _register_library_plugins(reg: PluginRegistry) -> None:
    """Discover and register plugins declared by libraries (steps, codecs,
    and anything else a library chooses to expose), on top of the built-in
    catalog above.

    This is the "know which plugins should be registered in runtime and lab
    libraries" mechanism: every library root discovered by
    :func:`mas.library_roots.discover_library_roots` (installed packages via
    the ``mas.runtime.manifest_libraries`` entry point, workspace
    ``manifest_libraries:`` config, and ``library.yaml`` directory scan) is
    checked for plugin manifests via :func:`mas.library_catalog.
    discover_plugin_manifests`, and each one is registered through the same
    generic fixpoint loader used for built-ins (see
    :func:`register_manifest_file`). A manifest can declare
    ``provides_types`` so a plugin from one library can introduce a brand
    new plugin *type* that a plugin from another library (or a later
    manifest) then registers against — no code change to this module is
    needed to add a new category.

    Manifest errors are not swallowed: a typo'd ``type:``/``module:``/
    ``class:`` in a library's plugin manifest is a real configuration
    error and should fail loudly at startup (see :func:`register_manifest_file`
    / :func:`_register_candidates_fixpoint`), not silently leave a plugin
    unregistered until something downstream fails with a confusing error.
    """
    from mas.library_catalog import discover_plugin_manifests

    for manifest_path in discover_plugin_manifests():
        _register_library_manifest(reg, manifest_path)


# Manifests already in the registry, so a later discovery adds only new ones.
_REGISTERED_MANIFESTS: set[Path] = set()
# Top-level plugin package names already claimed, including those registered
# at bootstrap, so a lab loaded later cannot silently share ``lib``.
_CLAIMED_PACKAGES: dict[str, Path] = {}


def _register_library_manifest(reg: PluginRegistry, manifest_path: Path) -> None:
    library_parent = str(manifest_path.parent.parent)
    if library_parent not in sys.path:
        sys.path.append(library_parent)
    try:
        data = _load_yaml(manifest_path)
        _claim_library_package(manifest_path.parent, data, _CLAIMED_PACKAGES)
        register_manifest_data(reg, data, source=manifest_path)
    except Exception as exc:
        raise ValueError(f"Failed to register plugin manifest {manifest_path}: {exc}") from exc
    _REGISTERED_MANIFESTS.add(manifest_path.resolve())


def register_new_library_plugins(reg: PluginRegistry) -> None:
    """Register plugin manifests that appeared after ``reg`` was built.

    The registry is built once per process. A long-lived process (the
    controller daemon) later loads labs whose own ``library.yaml`` declares
    pipeline steps; this adds those, and only those, to the live registry.
    """
    from mas.library_catalog import discover_plugin_manifests

    for manifest_path in discover_plugin_manifests():
        if manifest_path.resolve() not in _REGISTERED_MANIFESTS:
            _register_library_manifest(reg, manifest_path)


def clear_library_plugin_registration() -> None:
    """Forget manifests and plugin package names registered in this process.

    Tests call this with :func:`mas.library_roots.unregister_lab_dirs` so a
    later lab can reuse a package name. Production never calls it.
    """
    _REGISTERED_MANIFESTS.clear()
    _CLAIMED_PACKAGES.clear()


def _manifest_modules(data: dict[str, Any]) -> list[str]:
    modules: list[str] = []
    for item in data.get("plugins") or []:
        if not isinstance(item, dict):
            continue
        modules.append(str(item.get("module") or ""))
        for variant in (item.get("variants") or {}).values():
            if isinstance(variant, dict):
                modules.append(str(variant.get("module") or ""))
    return modules


def _claim_library_package(library_dir: Path, data: dict[str, Any], claimed: dict[str, Path]) -> None:
    """A library directory that is its own plugin package must own that top-level name.

    Library parents share one ``sys.path``: two libraries both shipping a
    ``lib`` package would silently load each other's modules.
    """
    name = library_dir.name
    if not any(m == name or m.startswith(name + ".") for m in _manifest_modules(data)):
        return
    here = library_dir.resolve()
    owner = claimed.setdefault(name, here)
    loaded = sys.modules.get(name)
    loaded_dirs = [Path(p).resolve() for p in getattr(loaded, "__path__", [])] if loaded else []
    other = owner if owner != here else next((d for d in loaded_dirs if d != here), None)
    if other is not None:
        raise ValueError(
            f"plugin package {name!r} at {here} clashes with {other}; give each library a unique package name"
        )


def register_plugin(
    reg: PluginRegistry,
    urn: str,
    cls: type,
    *,
    shortcuts: list[str] | None = None,
    variant: str = "builtin",
    description: str = "",
    attributes: dict[str, Any] | None = None,
) -> None:
    raw_plugin_type = (attributes or {}).get("plugin_type")
    plugin_type = _normalise_type_name(str(raw_plugin_type)) if raw_plugin_type else _plugin_type_from_urn(urn)
    if plugin_type:
        reg.register_type(plugin_type)
    entry = reg._entries.get(urn)
    module = cls.__module__
    class_name = cls.__name__
    if entry is None:
        entry = PluginEntry(
            urn=urn,
            description=description,
            default_variant=variant,
            shortcuts=shortcuts or [],
            variants={},
            attributes={"plugin_type": plugin_type, **dict(attributes or {})},
        )
        reg.register(entry)
    elif attributes:
        entry.attributes.update(attributes)
    entry.variants[variant] = VariantInfo(module=module, class_name=class_name)
    if shortcuts:
        for shortcut in shortcuts:
            reg.register_alias(shortcut, urn)


def register_manifest_data(
    reg: PluginRegistry,
    manifest_data: dict[str, Any],
    *,
    source: Path | None = None,
) -> None:
    known_types, candidates, role_aliases, defaults, runtime_spec_keys = _parse_generic_manifest(
        manifest_data, source=source
    )
    merged_known_types = set(reg._known_types)
    merged_known_types.update(known_types)
    reg.register_types(known_types)
    reg.register_runtime_spec_keys(runtime_spec_keys)
    for spec_key, plugin_id in defaults.items():
        reg.set_default(spec_key, plugin_id)
    _register_candidates_fixpoint(reg, merged_known_types, candidates)
    for role, urn in role_aliases.items():
        reg.register_alias(role, urn)


def register_manifest_file(reg: PluginRegistry, path: str | Path) -> None:
    path = Path(path)
    register_manifest_data(reg, _load_yaml(path), source=path)
