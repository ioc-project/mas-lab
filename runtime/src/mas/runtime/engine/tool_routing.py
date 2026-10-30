#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic tool-name routing across provider plugins.

The runtime is a name → provider map. Plugins register first; ``tools: "*"``
providers are queried at runtime initialization and the registry is updated
with the advertised names. Explicit lists skip that query.

The **local** plugin is the default. With no external providers, an implicit
local overlay owns every ``spec.tools`` name.

As soon as any external provider plugin is present, that implicit local overlay
is off. Every in-process name must be claimed:

- an external plugin (``tools: "*"`` after discovery, or an explicit list)
- or an explicit ``kind: local`` overlay (``tools: "*"`` = remaining names,
  or an explicit list)

Unclaimed names raise :class:`UnclaimedToolError`. Runtime system tools
(``request_human_input``, ``inform_user``, ``spawn_subagent``,
``create_subagent``) stay implicit-local.

Star claims are not checked at ``mas-ctl validate`` — presence is filled at
init. Explicit lists skip that query and can be checked at validate (names
against ``spec.tools``). Local YAML is re-checked at init. Arbitration (one
owner per name):

1. Explicit claims win over ``*``.
2. Two explicit claims on the same name is an error.
3. Two ``*`` plugins that both advertise the same unclaimed name is an error.
4. Local ``*`` only takes names externals did not claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SYSTEM_TOOL_NAMES: frozenset[str] = frozenset(
    {"request_human_input", "inform_user", "spawn_subagent", "create_subagent"}
)


class ToolRouteConflictError(ValueError):
    """Two providers claimed the same tool name with equal specificity."""


class UnclaimedToolError(ValueError):
    """A spec.tools name has no provider owner while an external plugin is present."""


class ExplicitToolUnavailableError(ValueError):
    """An explicit tools: [name, …] claim is missing from the provider at init."""


@dataclass(frozen=True)
class ProviderClaim:
    name: str
    advertised: tuple[str, ...]
    claim: str | tuple[str, ...]
    handler: Any
    origin: str = "external"  # "local" | "external"


@dataclass(frozen=True)
class ToolRoute:
    tool_name: str
    provider_name: str
    via: str  # "explicit" | "discover" | "local"
    handler: Any | None  # owning plugin; None only in routing unit tests


def is_star_claim(claim: str | tuple[str, ...] | None) -> bool:
    """True when the provider claims whatever it currently advertises."""
    return claim is None or claim == "*" or claim == ("*",)


def _explicit_tool_names(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        token = raw.strip()
        return (token,) if token and token != "*" else ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip() and str(item) != "*")
    return ()


def _advertised_tool_names(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        token = raw.strip()
        return (token,) if token else ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def tool_server_claim(server_id: str, server: dict[str, Any] | None) -> ProviderClaim:
    """Map one infra tool-server row onto the same claim object providers use."""
    payload = server or {}
    raw = payload.get("tools")
    if raw == "":
        raw = None
    sid = str(server_id)
    advertised = _advertised_tool_names(payload.get("advertised"))
    if is_star_claim(raw):
        return ProviderClaim(name=sid, advertised=advertised, claim="*", handler=sid, origin="external")
    names = _explicit_tool_names(raw)
    return ProviderClaim(name=sid, advertised=names, claim=names, handler=sid, origin="external")


def owner_for_tool(tool: str, providers: list[ProviderClaim]) -> str | None:
    """Provider that owns *tool*, or ``None`` if unclaimed or conflicting.

    A star claim with no advertised names fills *tool* for this lookup
    (one in-flight leftover), except runtime system tools, which stay
    implicit-local unless a server lists them. A star that already has
    advertised names wraps only those names. Two equal claims raise
    inside :func:`build_tool_routes`; this returns ``None`` so the dest
    wrap falls back to raw dispatch.
    """
    token = str(tool or "").strip()
    if not token:
        return None
    filled: list[ProviderClaim] = []
    for provider in providers:
        if (
            is_star_claim(provider.claim)
            and token not in provider.advertised
            and token not in SYSTEM_TOOL_NAMES
            and not provider.advertised
        ):
            filled.append(
                ProviderClaim(
                    provider.name,
                    tuple(provider.advertised) + (token,),
                    provider.claim,
                    provider.handler,
                    provider.origin,
                )
            )
        else:
            filled.append(provider)
    try:
        routes = build_tool_routes([], filled)
    except ToolRouteConflictError:
        return None
    route = routes.get(token)
    return None if route is None else route.provider_name


def claimed_names(claim: str | tuple[str, ...] | None, advertised: tuple[str, ...]) -> tuple[str, ...]:
    advertised_set = {n for n in advertised if n}
    if is_star_claim(claim):
        return tuple(sorted(advertised_set))
    wanted = [str(n) for n in (claim or ()) if n and n != "*"]
    return tuple(n for n in wanted if n in advertised_set)


def _put_explicit(routes: dict[str, ToolRoute], provider: ProviderClaim, name: str) -> None:
    existing = routes.get(name)
    if existing is not None and existing.provider_name != provider.name:
        if existing.via == "explicit":
            raise ToolRouteConflictError(
                f"tool {name!r} is claimed explicitly by both {existing.provider_name!r} and {provider.name!r}"
            )
        if existing.via == "discover":
            routes[name] = ToolRoute(name, provider.name, "explicit", provider.handler)
            return
        raise ToolRouteConflictError(
            f"tool {name!r} is claimed by both {existing.provider_name!r} and {provider.name!r}"
        )
    routes[name] = ToolRoute(name, provider.name, "explicit", provider.handler)


def build_tool_routes(
    local_names: list[str],
    providers: list[ProviderClaim],
    *,
    system_names: frozenset[str] = SYSTEM_TOOL_NAMES,
) -> dict[str, ToolRoute]:
    """Return the unique owner for each tool name.

    ``ProviderClaim.advertised`` for a ``*`` claim must already be the result of
    the runtime-init discovery query. Explicit claims pass the listed names as
    advertised; availability is checked separately (validate and/or init).
    """
    routes: dict[str, ToolRoute] = {}
    externals = [p for p in providers if p.origin != "local"]
    local_ps = [p for p in providers if p.origin == "local"]
    has_external = bool(externals)

    for provider in (p for p in externals if not is_star_claim(p.claim)):
        for name in claimed_names(provider.claim, provider.advertised):
            _put_explicit(routes, provider, name)

    for provider in (p for p in externals if is_star_claim(p.claim)):
        for name in claimed_names("*", provider.advertised):
            existing = routes.get(name)
            if existing is None:
                routes[name] = ToolRoute(name, provider.name, "discover", provider.handler)
                continue
            if existing.via == "explicit":
                continue
            if existing.provider_name != provider.name:
                raise ToolRouteConflictError(
                    f"tool {name!r} is advertised by both {existing.provider_name!r} "
                    f"and {provider.name!r} via tools: '*'; list the name on one provider"
                )

    if has_external:
        for provider in (p for p in local_ps if not is_star_claim(p.claim)):
            for name in claimed_names(provider.claim, provider.advertised):
                _put_explicit(routes, provider, name)

        for provider in (p for p in local_ps if is_star_claim(p.claim)):
            for name in claimed_names("*", provider.advertised):
                if name in routes:
                    continue
                routes[name] = ToolRoute(name, provider.name, "local", provider.handler)

        local_handler = next((p.handler for p in local_ps if p.handler is not None), None)
        for name in local_names:
            if name and name not in routes and name in system_names:
                routes[name] = ToolRoute(name, "local", "local", local_handler)

        leftover = [n for n in local_names if n and n not in routes]
        if leftover:
            listed = ", ".join(repr(n) for n in leftover)
            raise UnclaimedToolError(
                f"using tools not claimed by any provider: {listed}. "
                "Claim them on a provider plugin, or reintroduce in-process with "
                "a kind: local overlay (tools: '*' or an explicit list)."
            )
        return routes

    local_handler = next((p.handler for p in local_ps if p.handler is not None), None)
    for name in local_names:
        if name and name not in routes:
            routes[name] = ToolRoute(name, "local", "local", local_handler)
    return routes
