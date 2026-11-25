#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""System plugin ``identity``: lets the model list, describe, delegate and revoke the agent's own identities.

The tools work on handles. An identity is named by its id; what comes back is the value-free listing of
:class:`~mas.library.standard.lib.identity.surface.Surface`: ids, kinds, opaque subject references, status, grant
summaries and public facts. No tool returns evidence, a token, a header or any secret, whatever the model asks and
whatever a scheme holds, so a prompt that tries to get a credential out finds no tool that returns one. Presenting and
verifying stay at the protocol edge, automatic, and do not depend on the model.

The plugin is never on by default. An agent names it in ``spec.system_plugins``; ``tools`` limits it to some of the
four tools (a read-only agent lists ``identity_list`` and ``identity_describe``). Delegation and revocation are the
calls that change what the agent can do, so governance sees each one as an ordinary tool call, and the
``identity-guard`` plugin blocks them until a policy allows them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mas.library.standard.lib.identity import permits
from mas.library.standard.lib.identity.contract import IdentityError
from mas.library.standard.lib.identity.holders import scope_of
from mas.library.standard.lib.identity.surface import Surface
from mas.runtime.registry.provider_protocol import ManifestToolLoadError
from mas.runtime.system_plugins import (
    CallContext,
    SessionContext,
    SystemPluginBase,
    ToolDefinition,
    UnknownSystemToolError,
)

#: Largest id, record or scope the tools accept.
MAX_NAME = 256
#: Most scopes one delegation names.
MAX_SCOPES = 32

_NAME = {"type": "string", "minLength": 1, "maxLength": MAX_NAME}


def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "properties": dict(properties), "required": list(required)}


_RECORD = {"record": {**_NAME, "description": "The identity record, when two records list the same identity id."}}

DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="identity_list",
        description="List the identities this agent holds: id, kind, status, grant summary. Never a credential.",
        parameters=_schema(_RECORD),
        is_read_only=True,
        is_idempotent=True,
        is_open_world=False,
    ),
    ToolDefinition(
        name="identity_describe",
        description="The public facts of one identity (grade, limits, scopes, expiry). Never a credential.",
        parameters=_schema({"identity": _NAME, **_RECORD}, ["identity"]),
        is_read_only=True,
        is_idempotent=True,
        is_open_world=False,
    ),
    ToolDefinition(
        name="identity_delegate",
        description=(
            "Derive a narrower identity from one this agent holds, for the given scopes. The new identity can only do "
            "less than its parent. Returns its id, never a credential."
        ),
        parameters=_schema(
            {
                "identity": _NAME,
                "scopes": {"type": "array", "items": _NAME, "minItems": 1, "maxItems": MAX_SCOPES},
                "ttl_s": {"type": "number", "exclusiveMinimum": 0, "description": "Seconds the new identity lasts."},
                **_RECORD,
            },
            ["identity", "scopes"],
        ),
        is_read_only=False,
        is_destructive=False,
        is_idempotent=False,
        is_open_world=False,
    ),
    ToolDefinition(
        name="identity_revoke",
        description="Revoke one identity this agent holds. Sessions opened with it close. This cannot be undone.",
        parameters=_schema({"identity": _NAME, **_RECORD}, ["identity"]),
        is_read_only=False,
        is_destructive=True,
        is_idempotent=True,
        is_open_world=False,
    ),
)
_BY_NAME = {definition.name: definition for definition in DEFINITIONS}


@dataclass
class _State:
    surface: Surface


def _invalid(reason: str) -> IdentityError:
    return IdentityError("invalid", reason)


def _text(args: Mapping[str, Any], key: str, *, required: bool = False) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_NAME:
        raise _invalid(f"arg_{key}")
    return value


def _checked(tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments of ``tool``, or an ``invalid`` refusal: an unknown key is refused, never ignored."""
    allowed = set(_BY_NAME[tool].parameters["properties"])
    if set(args) - allowed:
        raise _invalid("args")
    out: dict[str, Any] = {"record": _text(args, "record")}
    if tool != "identity_list":
        out["identity"] = _text(args, "identity", required=True)
    if tool == "identity_delegate":
        scopes = args.get("scopes")
        if (
            not isinstance(scopes, list)
            or not 0 < len(scopes) <= MAX_SCOPES
            or not all(isinstance(scope, str) and 0 < len(scope) <= MAX_NAME for scope in scopes)
        ):
            raise _invalid("arg_scopes")
        out["scopes"] = scopes
        ttl = args.get("ttl_s")
        if ttl is not None and (
            isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0
        ):
            raise _invalid("arg_ttl_s")
        out["ttl_s"] = ttl
    return out


class IdentityPlugin(SystemPluginBase):
    """The identity tools over this session's agent. Settings: ``tools`` (a subset of the four tool names)."""

    name = "identity"

    def __init__(self, **settings: Any) -> None:
        unknown = set(settings) - {"tools"}
        if unknown:
            raise ManifestToolLoadError(f"identity: unknown setting {sorted(unknown)[0]!r}; the only setting is tools")
        names = settings.get("tools", list(_BY_NAME))
        if not isinstance(names, list) or not names or any(name not in _BY_NAME for name in names):
            raise ManifestToolLoadError(f"identity.tools is a non-empty list of {sorted(_BY_NAME)}")
        self._names = frozenset(names)
        self._state: _State | None = None

    def tools(self, ctx: Any | None = None) -> Sequence[ToolDefinition]:
        return tuple(definition for definition in DEFINITIONS if definition.name in self._names)

    def bind(self, session: SessionContext) -> object:
        self._state = _State(Surface(session.agent_id, scope=session.bind_scope or scope_of(session.spec)))
        return self._state

    def restore(self, snapshot: Mapping[str, Any]) -> object:
        if self._state is None:
            raise RuntimeError("IdentityPlugin.restore called before bind")
        return self._state

    async def acall(self, tool: str, args: Mapping[str, Any], *, state: object, context: CallContext) -> str:
        if tool not in self._names:
            raise UnknownSystemToolError(tool)
        surface = state.surface  # type: ignore[attr-defined]
        try:
            given = _checked(tool, args)
            if tool in permits.PERMIT_TOOLS and not permits.take(
                surface.scope, surface.agent, tool, context.correlation_id
            ):
                return "identity: refused:no_guard"  # no identity-guard decided about this call: the tool stays shut
            if tool == "identity_list":
                result: Any = surface.identities(given["record"])
            elif tool == "identity_describe":
                result = await surface.describe(given["identity"], given["record"])
            elif tool == "identity_delegate":
                result = await surface.delegate(
                    given["identity"], given["scopes"], record=given["record"], ttl_s=given["ttl_s"]
                )
            else:
                result = await surface.revoke(given["identity"], given["record"])
        except IdentityError as exc:
            return f"identity: {exc}"  # a code and a slug, never a value
        return json.dumps(result, sort_keys=True, default=str)
