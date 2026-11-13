#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Route tool calls across provider plugins.

This module is a façade over :class:`ToolProviderRegistry`: register plugins
from ``spec.providers[]``, then dispatch by name. It does not execute tools.
``kind`` on ``spec.providers[]`` is resolved through the plugin URN registry.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from mas.runtime.boundary.connection import PreparedOperation
from mas.runtime.contracts.user_communication_contract import HITLContract, UserIOContract
from mas.runtime.engine.tool_routing import UnclaimedToolError
from mas.runtime.layers.lifecycle import Lifecycle
from mas.runtime.registry.provider_protocol import ManifestToolLoadError, ToolProvider
from mas.runtime.registry.tool_provider_registry import (
    ToolProviderRegistry,
    provider_origin,
    tool_provider_class,
)
from mas.runtime.system_plugins import BoundSystemPlugins
from mas.runtime.system_plugins.dispatch import SystemPluginDispatcher

__all__ = [
    "ManifestToolLoadError",
    "ManifestToolProvider",
    "build_manifest_tool_provider",
    "attach_manifest_tools",
    "attach_manifest_tools_to_instance",
]


def _default_local_provider() -> Any:
    return tool_provider_class("local")()


class ManifestToolProvider(Lifecycle):
    """Name → provider dispatcher. Execution stays in the owning plugin."""

    seals_own_ctx = True  # the registry seals per provider; each tool gets the same ctx anywhere

    def __init__(self, overlay_providers: Optional[list[ToolProvider]] = None) -> None:
        self._registry = ToolProviderRegistry()
        providers = [_default_local_provider()] if overlay_providers is None else list(overlay_providers)
        for provider in providers:
            self._registry.register_provider(provider)

    def initialize(self, *, ctx: Any = None) -> None:
        """Runtime init: discover ``*`` claims and verify explicit lists."""
        self._registry.initialize(ctx=ctx)

    def _local_provider(self) -> Any | None:
        return self._registry.local_provider()

    @property
    def _tool_instances(self) -> list[Any]:
        local = self._local_provider()
        return list(getattr(local, "_tool_instances", []) or []) if local is not None else []

    @property
    def _overlay_providers(self) -> list[Any]:
        return self._registry.providers()

    def has_tools(self) -> bool:
        return self._registry.has_tools()

    def advertised_names_by_provider(self) -> dict[str, tuple[str, ...]]:
        return self._registry.advertised_names_by_provider()

    def system_plugins(self) -> BoundSystemPlugins | None:
        """System plugins the local provider bound, or ``None`` when it is not selected or binds none."""
        local = self._local_provider()
        return getattr(local, "system_plugins", None)

    def _add_instance(self, instance: Any, manifest_contract: dict[str, Any] | None) -> None:
        local = self._local_provider()
        if local is None:
            raise ManifestToolLoadError("local tool provider is not selected by the active infra dependencies")
        local._add_instance(instance, manifest_contract)
        self._registry.invalidate()

    def list_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        from mas.runtime.engine.tools import current_spec_for_advertise, disabled_tool_names

        tools = self._registry.list_tools(ctx=ctx)
        spec = current_spec_for_advertise(ctx)
        disabled = disabled_tool_names(spec)
        if not disabled:
            return tools
        return [tool for tool in tools if str(tool.get("name") or "") not in disabled]

    def list_openai_tools(self, *, ctx: Any = None) -> list[dict[str, Any]]:
        """OpenAI wire tools. Extra advertise keys (semantics, meta, …) stay off the payload."""
        out: list[dict[str, Any]] = []
        for spec in self.list_tools(ctx=ctx):
            name = str(spec.get("name") or "")
            if not name:
                continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(spec.get("description") or f"Invoke tool {name}."),
                        "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
                    },
                }
            )
        return out

    def assert_call_allowed(self, tool_name: str, *, ctx: Any = None) -> None:
        from mas.runtime.engine.tools import current_spec_for_advertise, disabled_tool_names

        if tool_name in disabled_tool_names(current_spec_for_advertise(ctx)):
            raise ManifestToolLoadError(f"tool {tool_name!r} is disabled in the current spec revision")
        self._registry.assert_call_allowed(tool_name, ctx=ctx)

    def prepare_call(self, tool_name: str, arguments: dict[str, Any], *, ctx: Any = None) -> PreparedOperation:
        self.assert_call_allowed(tool_name, ctx=ctx)
        return self._registry.prepare_call(tool_name, arguments, ctx=ctx)

    def stop(self) -> None:
        self.close()

    def close(self) -> None:
        for provider in self._registry.providers():
            closer = getattr(provider, "close", None)
            if callable(closer):
                closer()

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
        **kwargs: Any,
    ) -> Any:
        from mas.runtime.engine.tools import current_spec_for_advertise, disabled_tool_names

        if tool_name in disabled_tool_names(current_spec_for_advertise(ctx)):
            raise ManifestToolLoadError(f"tool {tool_name!r} is disabled in the current spec revision")
        try:
            return self._registry.call_tool(tool_name, arguments, ctx=ctx, user=user, **kwargs)
        except UnclaimedToolError:
            if self._registry.has_external_providers():
                raise
            raise ManifestToolLoadError(f"Tool {tool_name!r} not found in manifest or overlays") from None

    def probe_tool(self, tool_name: str, arguments: dict[str, Any], *, ctx: Any = None) -> Any:
        """Call a tool even though the running spec disabled it, to see whether it works again.

        Not a model call. The disabled-tool gate of :meth:`call_tool` is for the model; a layer that took the
        tool away is the one that may try it.
        """
        try:
            return self._registry.call_tool(tool_name, arguments, ctx=ctx, user="")
        except UnclaimedToolError:
            raise ManifestToolLoadError(f"Tool {tool_name!r} not found in manifest or overlays") from None

    async def acall_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
        **kwargs: Any,
    ) -> Any:
        from mas.runtime.engine.tools import current_spec_for_advertise, disabled_tool_names

        if tool_name in disabled_tool_names(current_spec_for_advertise(ctx)):
            raise ManifestToolLoadError(f"tool {tool_name!r} is disabled in the current spec revision")
        try:
            return await self._registry.acall_tool(tool_name, arguments, ctx=ctx, user=user, **kwargs)
        except UnclaimedToolError:
            if self._registry.has_external_providers():
                raise
            raise ManifestToolLoadError(f"Tool {tool_name!r} not found in manifest or overlays") from None


def build_manifest_tool_provider(
    tools_spec: list[Any],
    manifest_dir: Path,
    *,
    app_root: Path | None = None,
    system_plugins: Iterable[str] = (),
    spec: dict[str, Any] | None = None,
    hitl_contract: HITLContract | None = None,
    user_io_contract: UserIOContract | None = None,
    overlay_providers: Optional[list[ToolProvider]] = None,
    **containment_kw: Any,
) -> ManifestToolProvider:
    """Load ``spec.tools`` via the local plugin, then wrap with the generic router."""
    overlay = [_default_local_provider()] if overlay_providers is None else list(overlay_providers)
    local = next((p for p in overlay if provider_origin(p) == "local"), None)
    if local is None:
        local = _default_local_provider()
        overlay.append(local)
    load = getattr(local, "load_spec_tools", None)
    if callable(load):
        load(
            tools_spec,
            manifest_dir,
            app_root=app_root,
            system_plugins=system_plugins,
            spec=spec,
            hitl_contract=hitl_contract,
            user_io_contract=user_io_contract,
            **containment_kw,
        )
    return ManifestToolProvider(overlay_providers=overlay)


def attach_manifest_tools(
    engine: Any,
    manifest: dict | None,
    manifest_dir: Path | None,
    *,
    app_root: Path | None = None,
    **provider_kw: Any,
) -> ManifestToolProvider | None:
    """Load ``spec.tools`` and attach the routed provider to the leaf engine."""
    from mas.runtime.engine.leaf import leaf_engine
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.engine.tools import tools_with_resolved_names

    spec = (manifest or {}).get("spec") or {}
    overlay_providers = provider_kw.pop("overlay_providers", None)
    ctx = provider_kw.pop("ctx", None)
    if ctx is not None:
        from mas.runtime.engine.tools import (
            current_spec_for_advertise,
            is_spawn_subagent_enabled,
            spawn_subagent_params,
        )

        spec = current_spec_for_advertise(ctx, spec)
        ctx.current_spec = spec
        spawn_params = spawn_subagent_params(spec)
        ctx.allow_subagent_spawning = is_spawn_subagent_enabled(spec)
        ctx.subagent_templates = list((spawn_params or {}).get("templates") or [])
    if manifest_dir is None and spec.get("tools"):
        raise ManifestToolLoadError("manifest_dir is required when spec.tools is non-empty")
    tools = (
        tools_with_resolved_names(list(spec.get("tools") or []), manifest_dir)
        if manifest_dir
        else list(spec.get("tools") or [])
    )
    active_providers = list(overlay_providers or [])
    has_external = any(provider_origin(p) == "external" for p in active_providers)
    has_local = overlay_providers is None or any(provider_origin(p) == "local" for p in active_providers)
    tools_dir = provider_kw.get("tools_dir")
    discovers_local_tools = bool(
        has_local and tools_dir and Path(tools_dir).is_dir() and any(Path(tools_dir).rglob("*.tool.yaml"))
    )
    skills = list(spec.get("skills") or [])
    provider_kw.setdefault("skills_spec", skills)
    if not tools and not spec.get("system_plugins") and not has_external and not skills and not discovers_local_tools:
        return None

    provider_kw.setdefault("spec", spec)
    provider = build_manifest_tool_provider(
        tools,
        manifest_dir or Path("."),
        app_root=app_root or manifest_dir,
        overlay_providers=overlay_providers,
        **provider_kw,
    )
    bound = provider.system_plugins()
    if bound is not None:
        leaf = leaf_engine(engine)
        # a host contract that was on the engine before the plugins were bound moves under the plugin that owns it
        adopters = [adopt for row in bound if callable(adopt := getattr(row.state, "adopt", None))]
        contracts = tuple(
            contract
            for contract in getattr(leaf, "engine_tool_contracts", ())
            if not any(adopt(contract) for adopt in adopters)
        )
        leaf.engine_tool_contracts = (*contracts, SystemPluginDispatcher(bound))
        shared = bound.state_for_capability("delegation")
        wired = getattr(leaf, "delegation", None)
        if (
            shared is not None
            and wired is not None
            and wired is not shared
            and callable(getattr(shared, "adopt_wiring", None))
        ):
            # delegation was wired before the plugins were bound: the plugin's instance takes over
            shared.adopt_wiring(wired)
            leaf.delegation = shared
    provider.initialize(ctx=ctx)
    leaf = leaf_engine(engine)
    leaf.tool_provider = provider
    if isinstance(leaf, LiveLlmEngine):
        leaf.manifest_dir = manifest_dir
    return provider


def attach_manifest_tools_to_instance(
    instance: Any,
    manifest: dict | None,
    manifest_dir: Path | None,
    *,
    app_root: Path | None = None,
    **provider_kw: Any,
) -> ManifestToolProvider | None:
    engine = getattr(getattr(instance, "driver", None), "engine", None)
    if engine is None:
        return None
    ctx = getattr(getattr(instance, "driver", None), "ctx", None)
    return attach_manifest_tools(engine, manifest, manifest_dir, app_root=app_root, ctx=ctx, **provider_kw)
