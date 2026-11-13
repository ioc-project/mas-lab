#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Local tool-provider plugin — in-process Python ``spec.tools``.

Default ``kind: local`` binding from library-standard. The runtime registry
only maps names; this module loads ToolContract modules and executes them.
"""

from __future__ import annotations

import hashlib
import importlib.util as importlib_util
import inspect
import logging
import os
import sys
import threading
import types
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from mas.library.standard.plugins.tools.containment import (
    containment_roots,
    resolve_under_roots,
)
from mas.runtime.contracts.tool_contract import ToolContract, overlay_tool_advertise
from mas.runtime.contracts.user_communication_contract import HITLContract, UserIOContract
from mas.runtime.engine.tool_workers import arun_sync_tool, ctx_for_tool, run_awaitable
from mas.runtime.manifest.schema import ToolDocument
from mas.runtime.registry.provider_protocol import ManifestToolLoadError
from mas.runtime.system_plugins import (
    BoundSystemPlugins,
    CallContext,
    SessionContext,
    load_system_plugins,
    warn_deprecated_once,
)

logger = logging.getLogger(__name__)

_TOOL_MODULE_LOAD_LOCK = threading.RLock()


def _wrap_legacy_communication(
    tool: Any,
    communication: str | None,
    *,
    hitl_contract: HITLContract | None,
    user_io_contract: UserIOContract | None,
) -> Any:
    """Apply a leftover ``system_tool`` row's ``communication`` attribute."""
    if communication == "hitl":
        from mas.library.standard.plugins.system_tools.wrappers import SystemToolHitlWrapper

        return SystemToolHitlWrapper(tool, hitl_contract=hitl_contract)
    if communication == "user_io":
        from mas.library.standard.plugins.system_tools.wrappers import SystemToolUserUpdateWrapper

        return SystemToolUserUpdateWrapper(tool, user_io_contract=user_io_contract)
    return tool


def _containment_roots(
    manifest_dir: Path,
    app_root: Path | None,
    *,
    workspace_root: Path | None = None,
    tools_dir: Path | None = None,
) -> tuple[Path, ...]:
    return containment_roots(
        manifest_dir,
        app_root,
        workspace_root=workspace_root,
        tools_dir=tools_dir,
    )


def _resolve_under_roots(
    ref_base: Path,
    ref: str,
    *,
    containment_roots: tuple[Path, ...],
) -> Path:
    return resolve_under_roots(ref_base, ref, containment_roots=containment_roots)


def _tool_class_candidates(module: Any) -> list[type]:
    """Classes in *module* that expose tool collection (ToolContract or duck-typed)."""
    out: list[type] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if issubclass(obj, ToolContract) and obj is not ToolContract:
            out.append(obj)
            continue
        if callable(getattr(obj, "on_collect_tools", None)):
            out.append(obj)
    return out


def _is_async(fn: Any) -> bool:
    """True for ``async def``, also behind ``functools.wraps`` decorators that return the coroutine."""
    return inspect.iscoroutinefunction(inspect.unwrap(fn))


def _finished(result: Any) -> Any:
    """The value of a tool call: an ``async def`` tool is run to completion on the sync path."""
    return run_awaitable(result) if inspect.isawaitable(result) else result


class LocalToolProvider:
    """In-process Python tools loaded from ``spec.tools``.

    Same plugin contract as MCP: ``discover_tools`` / ``list_tools`` / ``call_tool``.
    """

    seals_own_ctx = True  # each tool instance is sealed or live by its own ``kernel_state``

    origin = "local"
    kind = "local"

    def __init__(
        self,
        name: str = "in-process",
        tools_claim: str | list[str] | None = "*",
        *,
        implicit: bool = True,
    ) -> None:
        self.provider_name = name
        self.implicit = implicit
        if tools_claim is None or tools_claim == "*":
            self.tools_claim: str | tuple[str, ...] = "*"
        elif isinstance(tools_claim, str):
            self.tools_claim = (tools_claim,)
        else:
            self.tools_claim = tuple(str(n) for n in tools_claim)
        self._tool_instances: list[Any] = []
        self._tool_contracts: dict[str, dict[str, Any]] = {}
        self.system_plugins: BoundSystemPlugins | None = None

    @classmethod
    def from_provider_spec(cls, spec: dict[str, Any]) -> "LocalToolProvider":
        """Bind an explicit ``kind: local`` ``spec.providers[]`` entry."""
        return cls(
            name=str(spec.get("name") or "in-process"),
            tools_claim=spec.get("tools", "*"),
            implicit=False,
        )

    @classmethod
    def from_tool_servers(cls, server_items: list[Any]) -> list["LocalToolProvider"]:
        """Bind the last explicit local infra server, if any."""
        local_servers = [
            server
            for server in server_items
            if isinstance(server, dict) and str(server.get("protocol") or "").lower() == "local"
        ]
        if not local_servers:
            return []
        local_server = local_servers[-1]
        spec = {**local_server, "name": str(local_server.get("id") or "local"), "kind": "local"}
        return [cls.from_provider_spec(spec)]

    def load_spec_tools(
        self,
        tools_spec: list[Any],
        manifest_dir: Path,
        **kwargs: Any,
    ) -> "LocalToolProvider":
        """Load ``spec.tools`` Python implementations into this provider."""
        return load_local_tool_provider(tools_spec, manifest_dir, provider=self, **kwargs)

    def has_tools(self) -> bool:
        if self.system_plugins is not None and (self.system_plugins.tool_names() or list(self.system_plugins)):
            return True
        return bool(self._tool_instances)

    def discover_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        return self.list_tools(ctx=ctx)

    def list_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        application = self._application_specs(ctx)
        if self.system_plugins is None:
            return application
        system = [spec for spec in self.system_plugins.advertise(ctx=ctx) if str(spec.get("name") or "")]
        seen = {str(spec["name"]) for spec in system}
        return system + [spec for spec in application if str(spec.get("name") or "") not in seen]

    def _application_specs(self, ctx: Any = None) -> list[dict[str, Any]]:
        """Advertise entries of the application tools (not the system plugins')."""
        result: list[dict[str, Any]] = []
        for instance in self._tool_instances:
            try:
                specs = instance.on_collect_tools(ctx=ctx)
                if specs is not None:
                    for spec in specs:
                        yaml_contract = self._tool_contracts.get(str(spec.get("name")))
                        result.append(overlay_tool_advertise(spec, yaml_contract))
                    continue
            except NotImplementedError:
                pass
            except Exception:
                raise
            try:
                legacy = {
                    "name": instance.get_name(),
                    "description": instance.get_description(),
                    "parameters": instance.get_parameters_schema(),
                }
                yaml_contract = self._tool_contracts.get(str(legacy["name"]))
                if yaml_contract:
                    legacy.update(yaml_contract)
                result.append(legacy)
            except Exception as exc:
                raise ManifestToolLoadError(f"Tool instance {instance!r} failed to describe itself: {exc}") from exc
        return result

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
        **kwargs: Any,
    ) -> Any:
        if self.system_plugins is not None and self.system_plugins.claims(tool_name):
            return self.system_plugins.call(tool_name, arguments, context=CallContext(ctx=ctx, user=user))
        for instance in self._tool_instances:
            owns = False
            legacy_match = False
            try:
                specs = instance.on_collect_tools(ctx=ctx)
                owns = any(s.get("name") == tool_name for s in (specs or []))
            except NotImplementedError:
                pass
            except Exception:
                raise
            if not owns:
                try:
                    legacy_match = instance.get_name() == tool_name
                except Exception:
                    legacy_match = False
            if not owns and not legacy_match:
                continue
            try:
                result = _finished(
                    instance.on_execute_tool(tool_name, arguments, ctx=ctx_for_tool(instance, ctx), user=user, **kwargs)
                )
                if result is not None:
                    return result
                if owns:
                    return ""
            except NotImplementedError:
                if legacy_match:
                    try:
                        return _finished(instance.execute(**arguments))
                    except NotImplementedError:
                        continue
                continue
            except Exception:
                raise
            if legacy_match:
                try:
                    return _finished(instance.execute(**arguments))
                except NotImplementedError:
                    continue
        raise ManifestToolLoadError(f"Tool {tool_name!r} not found in local provider")

    async def acall_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
        **kwargs: Any,
    ) -> Any:
        """Async twin of :meth:`call_tool`.

        One method is enough. ``on_aexecute_tool`` still wins when present.
        An async ``on_execute_tool`` (or ``execute``) is awaited on the loop.
        A sync ``on_execute_tool`` runs where the deployment says (inline or
        in a thread). The tool sees the same sealed context either way, except
        a ``kernel_state`` tool, which gets the live ctx and runs inline.
        """
        if self.system_plugins is not None and self.system_plugins.claims(tool_name):
            result = self.system_plugins.call(tool_name, arguments, context=CallContext(ctx=ctx, user=user))
            if inspect.isawaitable(result):
                result = await result
            return result
        owner = self._owner_of(tool_name, ctx)
        if owner is None:
            return self.call_tool(tool_name, arguments, ctx=ctx, user=user, **kwargs)
        aexec = getattr(owner, "on_aexecute_tool", None)
        if callable(aexec):
            result = aexec(tool_name, arguments, ctx=ctx_for_tool(owner, ctx), user=user, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return await self._none_result(owner, tool_name, arguments, ctx) if result is None else result
        exec_fn = getattr(owner, "on_execute_tool", None)
        execute = getattr(owner, "execute", None)
        if callable(exec_fn) and (_is_async(exec_fn) or _is_async(execute)):
            result = exec_fn(tool_name, arguments, ctx=ctx_for_tool(owner, ctx), user=user, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return await self._none_result(owner, tool_name, arguments, ctx) if result is None else result
        return await arun_sync_tool(self.call_tool, tool_name, arguments, owner=owner, ctx=ctx, user=user, **kwargs)

    async def _none_result(self, owner: Any, tool_name: str, arguments: dict[str, Any], ctx: Any) -> Any:
        """What ``call_tool`` does when ``on_execute_tool`` returns ``None``, for the async path.

        A tool that lists the name answered: the result is ``""``. A tool matched only by
        ``get_name()`` hands over to its legacy ``execute``.
        """
        try:
            listed = any(spec.get("name") == tool_name for spec in (owner.on_collect_tools(ctx=ctx) or []))
        except NotImplementedError:
            listed = False
        execute = getattr(owner, "execute", None)
        if listed or not callable(execute):
            return ""
        try:
            result = execute(**arguments)
            if inspect.isawaitable(result):
                result = await result
        except NotImplementedError:
            return ""
        return "" if result is None else result

    def _owner_of(self, tool_name: str, ctx: Any) -> Any | None:
        for instance in self._tool_instances:
            owns = False
            try:
                specs = instance.on_collect_tools(ctx=ctx)
                owns = any(s.get("name") == tool_name for s in (specs or []))
            except NotImplementedError:
                pass
            if not owns:
                try:
                    owns = instance.get_name() == tool_name
                except Exception:
                    owns = False
            if owns:
                return instance
        return None

    def _add_instance(
        self,
        instance: Any,
        manifest_contract: dict[str, Any] | None,
    ) -> None:
        if manifest_contract is not None:
            name = str(manifest_contract["name"])
            if name in self._tool_contracts:
                raise ManifestToolLoadError(f"duplicate manifest tool name {name!r}")
            self._tool_contracts[name] = manifest_contract
        self._tool_instances.append(instance)


class LocalToolClaim(LocalToolProvider):
    """Explicit ``kind: local`` overlay. Leftover names stay in-process."""

    def __init__(self, name: str = "in-process", tools_claim: str | list[str] | None = "*") -> None:
        super().__init__(name=name, tools_claim=tools_claim, implicit=False)


def load_local_tool_provider(
    tools_spec: list[Any],
    manifest_dir: Path,
    *,
    app_root: Path | None = None,
    system_plugins: Iterable[str] = (),
    system_tools: Iterable[str] = (),
    hitl_contract: HITLContract | None = None,
    user_io_contract: UserIOContract | None = None,
    provider: LocalToolProvider | None = None,
    **containment_kw: Any,
) -> LocalToolProvider:
    """Load ``spec.tools`` Python implementations into the local plugin.

    Enabled system plugins = ``spec.system_plugins`` + host-requested
    ``system_plugins`` + implicit skill plugins (``activate_skill`` when
    skills are listed, ``run_skill_script`` when one ships ``scripts/``).
    ``system_tools`` is the deprecated name of the host-requested ``system_plugins``.
    """
    local = provider or LocalToolProvider()
    skills_spec = containment_kw.pop("skills_spec", None)
    auto_inject_scripts = bool(containment_kw.pop("auto_inject_scripts", False))
    tools_dir_raw = containment_kw.pop("tools_dir", None)
    skills_dir = Path(containment_kw.pop("skills_dir", app_root or manifest_dir)).resolve()
    containment_kw.pop("spec_behavior", None)
    tools_dir = Path(tools_dir_raw).resolve() if tools_dir_raw else None

    if not tools_spec and tools_dir and tools_dir.is_dir():
        tools_spec = [{"ref": os.path.relpath(path, manifest_dir)} for path in sorted(tools_dir.rglob("*.tool.yaml"))]

    # request_human_input / inform_user stay opt-in: a model reaching for them
    # unprompted substitutes a non-answer the driving protocol cannot detect.
    spec = dict(containment_kw.pop("spec", None) or {})
    spec.setdefault("tools", list(tools_spec or []))
    if skills_spec is not None:
        spec.setdefault("skills", skills_spec)
    host_plugins = list(system_plugins)
    if system_tools:
        warn_deprecated_once("host-system-tools", "the system_tools= argument is deprecated: pass system_plugins=")
        host_plugins.extend(system_tools)
    local.system_plugins = load_system_plugins(
        spec,
        host_requested=host_plugins,
        session=SessionContext(
            spec=spec,
            hitl_contract=hitl_contract,
            user_io_contract=user_io_contract,
            skills_dir=skills_dir,
            auto_inject_scripts=auto_inject_scripts,
            wrap_system_tool=lambda tool, communication: _wrap_legacy_communication(
                tool,
                communication,
                hitl_contract=hitl_contract,
                user_io_contract=user_io_contract,
            ),
        ),
    )

    if not tools_spec:
        return local

    roots = _containment_roots(
        manifest_dir,
        app_root or manifest_dir,
        tools_dir=tools_dir,
        **containment_kw,
    )
    for index, raw in enumerate(tools_spec):
        if isinstance(raw, dict) and raw.get("kind") == "system":
            continue  # the deprecated spelling of a system plugin grant; load_system_plugins read it
        tool_def, mdir, manifest_contract = _normalize_tool_entry(
            raw,
            manifest_dir,
            index,
            containment_roots=roots,
            tools_dir=tools_dir,
        )
        module_path = tool_def.get("module_path")
        if not module_path:
            raise ManifestToolLoadError(f"spec.tools[{index}]: missing module_path after resolving entry {raw!r}")
        class_name = tool_def.get("class_name")
        params = dict(tool_def.get("params") or {})
        instance = _load_tool_instance(
            str(module_path),
            mdir,
            class_name=class_name,
            params=params,
            containment_roots=roots,
        )
        local._add_instance(instance, manifest_contract)
    system_names = local.system_plugins.tool_names() if local.system_plugins is not None else frozenset()
    if system_names:
        names = {str(spec.get("name") or "") for spec in local._application_specs()}
        overlap = names & set(system_names)
        if overlap:
            raise ManifestToolLoadError(f"application tools collide with system plugin tools {sorted(overlap)}")
    return local


def _normalize_tool_entry(
    raw: Any,
    manifest_dir: Path,
    index: int,
    *,
    containment_roots: tuple[Path, ...],
    tools_dir: Path | None = None,
) -> tuple[dict[str, Any], Path, dict[str, Any] | None]:
    catalog_ref_path: Path | None = None
    raw_entry_params: dict[str, Any] = {}
    if isinstance(raw, str):
        from mas.library_catalog import find_tool_manifest

        local_manifest = _find_tool_manifest_in_dir(raw, tools_dir)
        catalog_ref_path = local_manifest or find_tool_manifest(raw)
        if catalog_ref_path is None:
            local_hint = f"; checked local tools directory {tools_dir}" if tools_dir else ""
            raise ManifestToolLoadError(
                f"spec.tools[{index}]: tool name {raw!r} not found in any library "
                f"catalog (library.yaml tools:, tools/{raw}.tool.yaml, or "
                f"tools/{raw}/*.tool.yaml{local_hint}). Declare it in a library, or use "
                "{{ref: ./path.tool.yaml}} / inline module_path."
            )
        tool_def: dict[str, Any] = {}
    elif not isinstance(raw, dict):
        raise ManifestToolLoadError(f"spec.tools[{index}]: expected mapping, got {type(raw).__name__}")
    else:
        tool_def = dict(raw)
        raw_entry_params = dict(tool_def.get("params") or {})

    mdir = manifest_dir
    manifest_contract: dict[str, Any] | None = None

    if catalog_ref_path is not None or tool_def.get("ref"):
        # A bare name resolves through the trusted library tool catalog (the
        # infra name→implementation mapping) and is not subject to manifest
        # containment; an explicit {ref: ...} is resolved under the manifest's
        # containment roots.
        if catalog_ref_path is not None:
            ref_path = catalog_ref_path
        else:
            ref_path = _resolve_under_roots(mdir, str(tool_def["ref"]), containment_roots=containment_roots)
        if not ref_path.is_file():
            raise ManifestToolLoadError(f"spec.tools[{index}]: tool ref not found: {ref_path}")
        try:
            doc = yaml.safe_load(ref_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ManifestToolLoadError(f"spec.tools[{index}]: cannot read {ref_path}: {exc}") from exc
        if not isinstance(doc, dict):
            raise ManifestToolLoadError(f"spec.tools[{index}]: invalid tool YAML at {ref_path}")
        try:
            tool_contract = ToolDocument.from_dict(doc)
        except ValueError as exc:
            raise ManifestToolLoadError(f"spec.tools[{index}]: {exc}") from exc
        impl = (doc.get("spec") or {}).get("impl") or {}
        if not impl.get("module_path"):
            raise ManifestToolLoadError(f"spec.tools[{index}]: tool {ref_path} missing spec.impl.module_path")
        tool_name = tool_contract.name or ref_path.stem.replace(".tool", "")
        manifest_contract = tool_contract.to_contract_dict(tool_name)
        tool_def = {
            "module_path": impl.get("module_path"),
            "class_name": impl.get("class_name"),
            "params": dict(impl.get("params") or {}),
        }
        if raw_entry_params:
            merged_params = dict(tool_def["params"])
            merged_params.update(raw_entry_params)
            tool_def["params"] = merged_params
        mdir = ref_path.parent
    elif tool_def.get("module_path"):
        pass
    else:
        raise ManifestToolLoadError(f"spec.tools[{index}]: entry must include ref or module_path: {raw!r}")

    return tool_def, mdir, manifest_contract


def _find_tool_manifest_in_dir(name: str, tools_dir: Path | None) -> Path | None:
    if not tools_dir or not tools_dir.is_dir():
        return None
    root = tools_dir.resolve()
    candidates = (
        root / f"{name}.tool.yaml",
        root / name / f"{name}.tool.yaml",
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            return resolved
    tool_dir = (root / name).resolve()
    matches = sorted(tool_dir.glob("*.tool.yaml")) if tool_dir.is_relative_to(root) and tool_dir.is_dir() else []
    return matches[0].resolve() if matches else None


def _load_tool_instance(
    module_path: str,
    manifest_dir: Path,
    *,
    class_name: str | None,
    params: dict[str, Any],
    containment_roots: tuple[Path, ...],
) -> Any:
    is_file = (
        module_path.endswith(".py")
        or module_path.startswith((".", "/", "~"))
        or "/" in module_path
        or "\\" in module_path
    )
    if is_file:
        resolved = _resolve_under_roots(manifest_dir, module_path, containment_roots=containment_roots)
        if not resolved.is_file():
            raise ManifestToolLoadError(f"Tool module file not found: {resolved}")
        with _TOOL_MODULE_LOAD_LOCK:
            pkg_init = resolved.parent / "__init__.py"
            if pkg_init.exists():
                pkg_dir = resolved.parent
                pkg_hash = hashlib.sha1(str(pkg_dir).encode()).hexdigest()[:10]
                pkg_name = f"_mas_toolpkg_{pkg_dir.name}_{pkg_hash}"
                if pkg_name not in sys.modules:
                    pkg_mod = types.ModuleType(pkg_name)
                    pkg_mod.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
                    pkg_mod.__package__ = pkg_name
                    sys.modules[pkg_name] = pkg_mod
                key = f"{pkg_name}.{resolved.stem}"
                package_name = pkg_name
            else:
                key = f"_mas_tool_{resolved}"
                package_name = None
            if key in sys.modules:
                module = sys.modules[key]
            else:
                spec = importlib_util.spec_from_file_location(key, resolved)
                if spec is None or spec.loader is None:
                    raise ManifestToolLoadError(f"Cannot load tool module: {resolved}")
                module = importlib_util.module_from_spec(spec)
                if package_name is not None:
                    module.__package__ = package_name
                sys.modules[key] = module
                spec.loader.exec_module(module)
    else:
        fromlist = [class_name] if class_name else [""]
        try:
            module = __import__(module_path, fromlist=fromlist)
        except ModuleNotFoundError:
            module = _import_bundled_module(module_path)

    if class_name:
        tool_class = getattr(module, class_name)
    else:
        candidates = _tool_class_candidates(module)
        if not candidates:
            raise ManifestToolLoadError(
                f"No tool class found in {module_path} (class_name required, or define on_collect_tools)."
            )
        if len(candidates) > 1:
            names = ", ".join(c.__name__ for c in candidates)
            raise ManifestToolLoadError(f"multiple tool classes in {module_path}: {names}; specify class_name")
        tool_class = candidates[0]

    return tool_class(**params)


def _import_bundled_module(module_path: str) -> Any:
    """Resolve dotted module paths via importlib.resources when not on sys.path."""
    import importlib.resources as importlib_resources

    parts = module_path.split(".")
    if len(parts) < 2:
        raise ModuleNotFoundError(module_path)

    pkg_name = parts[0]
    sub_parts = parts[1:]

    try:
        pkg_root = importlib_resources.files(pkg_name)
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        raise ModuleNotFoundError(module_path) from None

    target = pkg_root
    for part in sub_parts[:-1]:
        target = target / part
    target = target / f"{sub_parts[-1]}.py"
    try:
        with importlib_resources.as_file(target) as resolved:
            if not resolved.is_file():
                raise ModuleNotFoundError(module_path)
            spec = importlib_util.spec_from_file_location(module_path, str(resolved))
            if spec is None or spec.loader is None:
                raise ModuleNotFoundError(f"Cannot load {resolved}")
            mod = importlib_util.module_from_spec(spec)
            sys.modules[module_path] = mod
            spec.loader.exec_module(mod)
            return mod
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        raise ModuleNotFoundError(module_path) from None
