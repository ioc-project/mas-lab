#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tool-provider registry — name → provider routing engine.

Plugins register here. Runtime calling is a lookup, not execution:

1. ``register_provider`` — record the plugin (no tool query).
2. ``initialize`` — for ``tools: "*"``, query advertised names and bind them;
   for an explicit list, bind the claimed names (local YAML is listed to
   confirm those names exist; MCP does not query ``tools/list``).

``mas-ctl validate`` never calls ``initialize``. Star claims therefore always
pass verification. Explicit lists can be checked at validate (names in
``spec.tools``). Runtime init does not query ``tools/list`` for an explicit
claim; local YAML is still listed to confirm those names exist.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from mas.runtime.contracts.tool_contract import invoke_call_tool
from mas.runtime.engine.tool_routing import (
    ExplicitToolUnavailableError,
    ProviderClaim,
    UnclaimedToolError,
    build_tool_routes,
    is_star_claim,
)
from mas.runtime.registry.provider_protocol import ToolProvider

logger = logging.getLogger(__name__)

# Connection / list-transport fields live on infra ToolServerRegistry, not
# ToolContract. Overlay providers[] may set the same keys; overlay values win.
_TOOL_SERVER_CONNECTION_KEYS = (
    "url",
    "endpoint",
    "transport",
    "command",
    "args",
    "env",
    "cwd",
    "headers",
    "timeout",
    "follow_pagination",
    "cache_ttl_ms",
    "cache_scope",
)


def _connection_unset(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def index_tool_servers(
    servers: dict[str, Any] | list[Any] | None,
) -> dict[str, dict[str, Any]]:
    """Index infra ``tool_servers[]`` by ``id`` (and ``name`` when distinct)."""
    indexed: dict[str, dict[str, Any]] = {}
    items: list[Any]
    if isinstance(servers, dict) and isinstance(servers.get("tool_servers"), list):
        items = list(servers["tool_servers"])
    elif isinstance(servers, dict):
        items = []
        for key, value in servers.items():
            if not isinstance(value, dict):
                continue
            payload = dict(value)
            indexed[str(key)] = payload
            items.append(payload)
    else:
        items = list(servers or [])
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        for key in (payload.get("id"), payload.get("name")):
            ident = str(key or "").strip()
            if ident:
                indexed[ident] = payload
    return indexed


def apply_tool_server_defaults(
    provider: dict[str, Any],
    servers: dict[str, Any] | list[Any] | None,
) -> dict[str, Any]:
    """Fill unset connection fields from a matching infra tool-server.

    Match is ``providers[].name`` → ``tool_servers[].id`` (or ``name``).
    Keys already set on the provider win over the matching infra server.
    """
    if not provider:
        return provider
    indexed = index_tool_servers(servers)
    if not indexed:
        return provider
    ident = str(provider.get("name") or "").strip()
    server = indexed.get(ident)
    if not server:
        return provider
    out = dict(provider)
    for key in _TOOL_SERVER_CONNECTION_KEYS:
        if key not in server:
            continue
        if _connection_unset(out.get(key)):
            out[key] = server[key]
    if _connection_unset(out.get("url")) and not _connection_unset(server.get("endpoint")):
        out["url"] = server["endpoint"]
    return out


def provider_origin(provider: Any) -> str:
    origin = str(getattr(provider, "origin", "") or "")
    kind = str(getattr(provider, "kind", "") or "")
    if origin == "local" or kind == "local":
        return "local"
    return "external"


def provider_name(provider: Any) -> str:
    return str(getattr(provider, "provider_name", None) or provider.__class__.__name__)


def _tool_names(specs: list[dict[str, Any]] | None) -> tuple[str, ...]:
    return tuple(str(s.get("name") or "") for s in (specs or []) if s.get("name"))


class ToolProviderRegistry:
    """Registry mapping tool names to the provider plugin that owns them."""

    def __init__(self) -> None:
        self._providers: List[Any] = []
        self._routes: dict[str, Any] | None = None

    def register_provider(self, provider: ToolProvider) -> None:
        """Step 1: register a plugin. Does not query tools."""
        if not callable(getattr(provider, "list_tools", None)) or not callable(getattr(provider, "call_tool", None)):
            raise TypeError(f"Provider must implement list_tools and call_tool, got {type(provider)}")
        self._providers.append(provider)
        self._routes = None
        logger.debug("Registered tool provider: %s", provider_name(provider))

    def unregister_provider(self, provider: ToolProvider) -> None:
        if provider in self._providers:
            self._providers.remove(provider)
            self._routes = None
            logger.debug("Unregistered tool provider: %s", provider_name(provider))

    def invalidate(self) -> None:
        """Drop the name map so the next list/call re-runs initialize."""
        self._routes = None
        for provider in self._providers:
            drop = getattr(provider, "invalidate", None)
            if callable(drop):
                drop()

    def providers(self) -> List[ToolProvider]:
        return list(self._providers)

    def local_provider(self) -> Any | None:
        for provider in self._providers:
            if provider_origin(provider) == "local":
                return provider
        return None

    def has_external_providers(self) -> bool:
        return any(provider_origin(p) == "external" for p in self._providers)

    def has_tools(self) -> bool:
        if self._routes is not None:
            return bool(self._routes)
        return any(bool(getattr(p, "has_tools", lambda: True)()) for p in self._providers)

    def initialize(self, *, ctx: Any = None) -> None:
        """Step 2: bind names. Star providers are queried now; explicit lists skip that query."""
        if self._routes is not None:
            return
        local = self.local_provider()
        local_specs = local.list_tools(ctx=ctx) if local is not None else []
        local_names = [n for n in _tool_names(local_specs) if n]
        self._routes = build_tool_routes(local_names, self._provider_claims(local_names, ctx=ctx))

    def _discover_advertised(self, provider: Any, name: str, *, ctx: Any = None) -> tuple[str, ...]:
        discover = getattr(provider, "discover_tools", None) or provider.list_tools
        try:
            advertised_specs = discover(ctx=ctx) or []
        except Exception as exc:
            logger.error("Error discovering tools from provider %s: %s", name, exc, exc_info=True)
            raise RuntimeError(
                f"provider {name!r} advertised tools: '*' but discovery failed; refusing local fallback: {exc}"
            ) from exc
        return _tool_names(advertised_specs)

    def _verify_explicit(self, provider: Any, name: str, claimed: tuple[str, ...], *, ctx: Any = None) -> None:
        """Confirm an explicit list is available. Uses list_tools, not discover_tools."""
        try:
            available = set(_tool_names(provider.list_tools(ctx=ctx) or []))
        except Exception as exc:
            raise ExplicitToolUnavailableError(
                f"provider {name!r} claims {list(claimed)} but availability check failed: {exc}"
            ) from exc
        missing = [n for n in claimed if n not in available]
        if missing:
            listed = ", ".join(repr(n) for n in missing)
            raise ExplicitToolUnavailableError(f"provider {name!r} claims tools not available: {listed}")

    def _provider_claims(self, local_names: list[str], *, ctx: Any = None) -> list[ProviderClaim]:
        has_external = self.has_external_providers()
        external_claims: list[ProviderClaim] = []
        local_claims: list[ProviderClaim] = []
        for provider in self._providers:
            if provider_origin(provider) == "local":
                continue
            name = provider_name(provider)
            claim = getattr(provider, "tools_claim", "*")
            if is_star_claim(claim):
                advertised = self._discover_advertised(provider, name, ctx=ctx)
            else:
                advertised = tuple(
                    str(n) for n in (claim if not isinstance(claim, str) else (claim,)) if n and n != "*"
                )
                self._verify_explicit(provider, name, advertised, ctx=ctx)
            external_claims.append(
                ProviderClaim(
                    name=name,
                    advertised=advertised,
                    claim=claim,
                    handler=provider,
                    origin="external",
                )
            )
        externally_claimed = {name for claim in external_claims for name in claim.advertised}
        for provider in self._providers:
            if provider_origin(provider) != "local":
                continue
            name = provider_name(provider)
            claim = getattr(provider, "tools_claim", "*")
            if has_external and is_star_claim(claim):
                advertised = tuple(n for n in local_names if n and n not in externally_claimed)
                claim = advertised
            else:
                advertised = tuple(n for n in local_names if n) if is_star_claim(claim) else tuple(
                    str(n) for n in (claim if not isinstance(claim, str) else (claim,)) if n and n != "*"
                )
                if not is_star_claim(getattr(provider, "tools_claim", "*")):
                    self._verify_explicit(provider, name, advertised, ctx=ctx)
            local_claims.append(
                ProviderClaim(
                    name=name,
                    advertised=advertised,
                    claim=claim,
                    handler=provider,
                    origin="local",
                )
            )
        return external_claims + local_claims

    def _spec_from_handler(self, handler: Any, tool_name: str, *, ctx: Any = None) -> dict[str, Any] | None:
        try:
            for spec in handler.list_tools(ctx=ctx) or []:
                if str(spec.get("name") or "") == tool_name:
                    return self._with_advertised_schema(handler, spec, ctx=ctx)
        except Exception as exc:
            logger.error("Error listing tools from provider %s: %s", handler, exc, exc_info=True)
        return None

    @staticmethod
    def _with_advertised_schema(handler: Any, spec: dict[str, Any], *, ctx: Any = None) -> dict[str, Any]:
        """Give the model real parameters for an explicitly claimed tool.

        An explicit claim routes by name without asking the server, so its
        ``list_tools`` entry has an empty schema. When the spec is built for the
        model, ask the provider's own advertisement (``discover_tools``) for that
        tool. A failed lookup keeps the bare entry.
        """
        params = spec.get("parameters")
        if params and (params.get("properties") or params.get("required")):
            return spec
        discover = getattr(handler, "discover_tools", None)
        if not callable(discover):
            return spec
        try:
            for found in discover(ctx=ctx) or []:
                if str(found.get("name") or "") == str(spec.get("name") or ""):
                    return found
        except Exception:  # noqa: BLE001 - the bare entry still routes
            logger.debug("schema lookup failed for tool %r", spec.get("name"), exc_info=True)
        return spec

    def list_tools(self, *, ctx: Any = None) -> List[Dict[str, Any]]:
        """Return the spec for each routed name from its owning provider."""
        self.initialize(ctx=ctx)
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for name, route in (self._routes or {}).items():
            if not name or name in seen:
                continue
            if route.handler is None:
                continue
            routed = self._spec_from_handler(route.handler, name, ctx=ctx)
            if routed:
                out.append(routed)
                seen.add(name)
        return out

    def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        ctx: Any = None,
        user: str = "",
        **kwargs: Any,
    ) -> Any:
        """Dispatch by name. The owning plugin executes; this registry does not."""
        self.initialize(ctx=ctx)
        route = (self._routes or {}).get(tool_name)
        if route is None or route.handler is None:
            if self.has_external_providers():
                raise UnclaimedToolError(
                    f"using tools not claimed by any provider: {tool_name!r}. "
                    "Claim it on a provider plugin, or reintroduce in-process with a kind: local overlay."
                )
            raise UnclaimedToolError(f"Tool {tool_name!r} is not registered with any provider")
        return invoke_call_tool(
            route.handler.call_tool,
            tool_name,
            arguments,
            ctx=ctx,
            user=user,
            **kwargs,
        )


def iter_provider_specs(manifest_content: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Yield ``providers[]`` from a raw or merged agent manifest.

    Merged agents keep providers on ``spec.providers``. Top-level ``providers``
    is a fallback. When both are present, entries are unioned by ``name`` and
    the spec entry wins.
    """
    if not manifest_content:
        return []
    spec_block = manifest_content.get("spec") or {}
    spec_list = list(spec_block.get("providers") or []) if isinstance(spec_block, dict) else []
    top = list(manifest_content.get("providers") or [])
    if spec_list and top:
        by_name: dict[str, dict[str, Any]] = {}
        unnamed: list[dict[str, Any]] = []
        for item in top + spec_list:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("name") or "").strip()
            if ident:
                by_name[ident] = item
            else:
                unnamed.append(item)
        return unnamed + list(by_name.values())
    raw = spec_list or top
    return [item for item in raw if isinstance(item, dict)]


def tool_provider_class(kind: str) -> type:
    """Resolve ``spec.providers[].kind`` through the plugin URN registry."""
    from mas.runtime.registry import get_registry

    info = get_registry().resolve_by_type("tool_provider", kind)
    if info is None:
        raise KeyError(f"no tool_provider plugin registered for kind {kind!r}")
    return info.load_class()


def providers_from_manifest(
    manifest_content: dict[str, Any] | None,
    *,
    tool_servers: dict[str, Any] | list[Any] | None = None,
) -> list[Any]:
    """Instantiate local/MCP providers from infra dependencies.

    The implicit local server is the default. Any MCP ``usage: use`` server
    replaces it. An explicit local server with a ``tools`` claim re-adds local
    alongside MCP. Deployment-only servers are not MCP clients.

    Existing provider declarations are retained only for internal callers that
    bypass manifest validation. ``tool_servers`` may be the resolved infra
    index (id → item) or a ToolServerRegistry manifest.
    """
    specs = iter_provider_specs(manifest_content)
    if specs:
        providers: list[Any] = []
        for spec in specs:
            merged = apply_tool_server_defaults(spec, tool_servers)
            kind = str(merged.get("kind") or merged.get("type") or "").strip()
            if not kind:
                raise ValueError("spec.providers entry is missing kind")
            cls = tool_provider_class(kind)
            factory = getattr(cls, "from_provider_spec", None)
            providers.append(factory(merged) if callable(factory) else cls())
        return providers

    if isinstance(tool_servers, dict) and isinstance(tool_servers.get("tool_servers"), list):
        server_items = list(tool_servers["tool_servers"])
    elif isinstance(tool_servers, dict):
        server_items = list(tool_servers.values())
    else:
        server_items = list(tool_servers or [])

    usable_servers = [
        server
        for server in server_items
        if isinstance(server, dict)
        and str(server.get("protocol") or "").lower() == "mcp"
        and server.get("usage", "use") in {"use", "use-and-deploy"}
    ]
    local_servers = [
        server
        for server in server_items
        if isinstance(server, dict)
        and str(server.get("protocol") or "").lower() == "local"
    ]
    providers = []
    if usable_servers:
        seen: set[str] = set()
        for server in usable_servers:
            ident = str(server.get("id") or server.get("name") or "").strip()
            if not ident or ident in seen:
                continue
            seen.add(ident)
            spec = dict(server)
            spec.update(name=ident, kind="mcp")
            spec.setdefault("tools", "*")
            providers.append(tool_provider_class("mcp").from_provider_spec(spec))
    if not usable_servers or local_servers:
        local_class = tool_provider_class("local")
        local_server = local_servers[-1] if local_servers else {}
        if local_server:
            factory = getattr(local_class, "from_provider_spec", None)
            local_spec = {**local_server, "name": str(local_server.get("id") or "local"), "kind": "local"}
            local_provider = factory(local_spec) if callable(factory) else local_class()
        else:
            local_provider = local_class(name="local", implicit=True)
        providers.append(local_provider)
    if not providers:
        from mas.runtime.registry import get_registry

        default_kind = get_registry().default_for("tool_provider") or "local"
        return [tool_provider_class(default_kind)()]
    return providers
