#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Flavour ``bind``: which infra rows each agent's calls ride, and what this deployment does with it.

Infra rows are servers and endpoints named by what they are (a message-bus node, an A2A
server), never by an agent. A row's ``usage`` is its capability: ``connect``, a
server we can dial that serves agents; ``listen``, infrastructure that can host
agents we run (default ``connect``, as for every Application row). A Flavour
links agents to rows: ``spec.bind.agent_comm`` is the default chain, bottom
first, and ``spec.bind.agents.<id>`` gives one agent its own ``agent_comm``
chain, the ``usage`` this deployment makes of it (``connect``: reach it there;
``listen``: host it here), and ``params`` that override a row's params for it.
A row's string params may hold ``{agent}``, replaced by each agent's id, so one
row serves many agents. Each row of a chain is a layer: its ``protocol`` is the
layer plugin, its other fields are the layer's params. ctl reads no protocol
here: the plugin registry resolves every type, and the layer class says what it
needs.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from mas.ctl.compose.models import ResolvedInfra

# Words a key that names a secret ends in; ``[_-]?`` is an optional separator inside the word.
SECRET_WORDS = (
    "secret",
    "password",
    "passwd",
    "passphrase",
    "token",
    "api[_-]?key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "private[_-]?key",
)


def _any_case(word: str) -> str:
    return "".join(f"[{ch.upper()}{ch.lower()}]" if ch.isalpha() else ch for ch in word)


def _capitalized(word: str) -> str:
    return word[0].upper() + _any_case(word[1:])


# A key that names a secret: one of the words in any case, alone or after ``_`` / ``-`` (``access_token``,
# ``X-Api-Key``, ``Set-Cookie``), or capitalized after a lower-case letter or a digit (``accessToken``,
# ``clientSecret``). Letter classes instead of a case flag, so the JSON Schema
# (docs/schemas/runtime/fragments/application-endpoint.schema.yaml) holds this very pattern.
SECRET_KEY = re.compile(
    "^((.*[_-])?(" + "|".join(_any_case(w) for w in SECRET_WORDS) + ")"
    "|.*[a-z0-9](" + "|".join(_capitalized(w) for w in SECRET_WORDS) + "))$"
)


class BindError(ValueError):
    """A Flavour ``bind`` that names a row it cannot use."""


def _url_secrets(key: str, value: str) -> list[str]:
    """What in a url string would carry a secret: credentials, or a query key that names one."""
    if "://" not in value:
        return []
    parsed = urlsplit(value)
    found = [f"{key} (url credentials)"] if "@" in parsed.netloc else []
    found += [f"{key} (url query {name!r})" for name, _ in parse_qsl(parsed.query) if SECRET_KEY.match(name)]
    return found


def secret_keys(value: Any, key: str = "") -> list[str]:
    """Keys that name a secret, and url params that carry one (credentials, a secret query key), at any depth."""
    found: list[str] = []
    if isinstance(value, dict):
        for name, item in value.items():
            if SECRET_KEY.match(str(name)):
                found.append(str(name))
            found.extend(secret_keys(item, str(name)))
    elif isinstance(value, list):
        for item in value:
            found.extend(secret_keys(item, key))
    elif isinstance(value, str):
        found.extend(_url_secrets(key, value))
    return found


def _row_names(raw: Any, where: str) -> list[str]:
    if not isinstance(raw, list) or not raw or not all(isinstance(name, str) and name.strip() for name in raw):
        raise BindError(f"Flavour {where} is a list of Application row names, bottom first")
    names = [name.strip() for name in raw]
    if len(set(names)) != len(names):
        raise BindError(f"Flavour {where} names a row twice")
    return names


USAGES = ("connect", "listen")
DEFAULT_USAGE = ["connect"]


def _usage(raw: Any, where: str) -> list[str]:
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or not values or any(value not in USAGES for value in values):
        raise BindError(f"Flavour {where}.usage is connect, listen, or [connect, listen]")
    return [usage for usage in USAGES if usage in values]


def bound_chains(flavour_spec: dict[str, Any] | None) -> tuple[list[str], list[str], dict[str, dict[str, Any]]]:
    """The default row names of ``spec.bind.agent_comm``, the default usage, and each ``spec.bind.agents.<id>`` entry.

    An entry is ``{agent_comm: [rows] | None, usage: [..] | None, params: {row: {param: value}}}``.
    """
    bind = (flavour_spec or {}).get("bind")
    if bind is None:
        return [], list(DEFAULT_USAGE), {}
    if not isinstance(bind, dict):
        raise BindError("Flavour bind must be a map")
    unknown = sorted(set(bind) - {"agent_comm", "agents", "usage", "events"})
    if unknown:
        raise BindError(f"Flavour bind: unknown keys {unknown}")
    default = _row_names(bind["agent_comm"], "bind.agent_comm") if bind.get("agent_comm") is not None else []
    default_usage = _usage(bind["usage"], "bind") if "usage" in bind else list(DEFAULT_USAGE)
    agents = bind.get("agents") or {}
    if not isinstance(agents, dict):
        raise BindError("Flavour bind.agents is a map of agent id to {agent_comm, usage, params}")
    entries: dict[str, dict[str, Any]] = {}
    for agent_id, entry in agents.items():
        where = f"bind.agents.{agent_id}"
        if not isinstance(entry, dict) or not entry or set(entry) - {"agent_comm", "usage", "params"}:
            raise BindError(f"Flavour {where} takes agent_comm (a list of Application row names), usage and params")
        params = entry.get("params") or {}
        if not isinstance(params, dict) or not all(isinstance(value, dict) for value in params.values()):
            raise BindError(f"Flavour {where}.params is a map of row name to {{param: value}}")
        entries[str(agent_id)] = {
            "agent_comm": _row_names(entry["agent_comm"], f"{where}.agent_comm") if "agent_comm" in entry else None,
            "usage": _usage(entry["usage"], where) if "usage" in entry else None,
            "params": {str(row): dict(values) for row, values in params.items()},
        }
    return default, default_usage, entries


def _named_side_rows(
    bind: dict[str, Any],
    key: str,
    *,
    rows: dict[str, Any],
) -> list[str]:
    """Row names of ``bind.events``; each name must exist as an Application row."""
    if key not in bind or bind.get(key) is None:
        return []
    names = _row_names(bind[key], f"bind.{key}")
    for name in names:
        if name not in rows:
            raise BindError(f"Flavour bind.{key} names {name!r}, which is no Application row")
    return names


def flavour_spec_of(flavour: Any) -> dict[str, Any]:
    """The ``spec`` of a Flavour document, or the mapping itself when it already is one."""
    if not isinstance(flavour, dict):
        return {}
    spec = flavour.get("spec")
    return spec if isinstance(spec, dict) else flavour


def _layer(name: str, rows: dict[str, Any], where: str) -> dict[str, Any]:
    from mas.ctl.infra.resolve import application_endpoint_usage

    row = rows.get(name)
    if not isinstance(row, dict):
        raise BindError(f"Flavour {where} names {name!r}, which is no Application row")
    protocol = str(row.get("protocol") or "").strip()
    if not protocol:
        raise BindError(f"Application {name!r}: a row in {where} needs protocol, the layer it is")
    try:
        capability = sorted(application_endpoint_usage(row))
    except ValueError as exc:
        raise BindError(f"Application {name!r}: {exc}") from None
    params = {key: value for key, value in row.items() if key not in {"protocol", "usage", "expose"}}
    secrets = sorted(set(secret_keys(params)))
    if secrets:
        raise BindError(f"Application {name!r}: {secrets} would hold a secret; name its env var with secret_env")
    return {"row": name, "type": protocol, "params": params, "usage": capability}


_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
# An agent id fills {agent} as it is, so it may only hold characters that mean the same in a url path,
# a host label and a name part: letters, digits, '.', '_' and '-', and not dots alone ('.' and '..' are
# path segments a url normalizes away). Nothing is encoded.
SAFE_AGENT_ID = re.compile(r"^(?!\.+$)[A-Za-z0-9._-]+$")


def _for_agent(chain: list[dict[str, Any]], agent_id: str, overrides: dict[str, dict[str, Any]], where: str):
    """``chain`` as ``agent_id`` rides it: its ``params`` overrides applied, then ``{agent}`` replaced."""
    unknown = sorted(set(overrides) - {layer["row"] for layer in chain})
    if unknown:
        raise BindError(f"Flavour {where}.params names {unknown[0]!r}, which is not a row of the agent's chain")
    out: list[dict[str, Any]] = []
    for layer in chain:
        params = {**layer["params"], **overrides.get(layer["row"], {})}
        secrets = sorted(set(secret_keys(params)))
        if secrets:
            raise BindError(f"Flavour {where}.params: {secrets} would hold a secret; name its env var with secret_env")
        resolved: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str):
                for placeholder in _PLACEHOLDER.findall(value):
                    if placeholder != "agent":
                        raise BindError(
                            f"Application {layer['row']!r}: {key} holds {{{placeholder}}}; "
                            "the only placeholder is {agent}"
                        )
                if "{agent}" in value and not SAFE_AGENT_ID.match(agent_id):
                    raise BindError(
                        f"Application {layer['row']!r}: agent id {agent_id!r} cannot fill {{agent}} in {key}; "
                        "an id there holds only letters, digits, '.', '_' and '-', and not dots alone"
                    )
                value = value.replace("{agent}", agent_id)
            resolved[key] = value
        out.append({**layer, "params": resolved})
    if any(layer["params"] != raw["params"] for layer, raw in zip(out, chain, strict=True)):
        name = f"{'+'.join(layer['row'] for layer in chain)}@{agent_id}"
    else:
        name = "+".join(layer["row"] for layer in chain)
    for layer in out:
        layer["chain"] = name
    return out


def _planned_chains(
    wheres: dict[str, list[str]], rows: dict[str, Any], known: list[str]
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Each named chain of the Flavour as layers, keyed by where it is named, and which of them are direct links.

    A chain is refused when it names an agent of the session, a row that is not a layer, a transport above
    the bottom, a first layer that is no transport, or ``listen`` on a bottom that only connects.
    """
    from mas.library.standard.lib.stack import StackError
    from mas.library.standard.lib.stack.handle import is_direct_link, layer_classes, listen_refusal

    raw: dict[str, list[dict[str, Any]]] = {}
    direct: set[str] = set()
    for where, names in wheres.items():
        if not names:
            continue
        for name in names:
            if name in known:
                raise BindError(
                    f"Application {name!r}: {where} names a row that is also an agent of this session; "
                    "name an infra row by the endpoint it is"
                )
        chain = [_layer(name, rows, where) for name in names]
        try:
            classes = layer_classes(chain)
        except StackError as exc:
            raise BindError(f"Flavour {where}: {exc}") from None
        if len(chain) == 1 and is_direct_link(classes[0]):
            # A one-row chain on a link that reaches one agent at its own url (the A2A SDK client)
            # is the released direct path: the agent-keyed row, not a stack.
            if where == "bind.agent_comm":
                raise BindError(
                    f"Flavour bind.agent_comm: {names[0]!r} is a link to one agent's own url; "
                    "link that agent with bind.agents.<id>.agent_comm instead"
                )
            raw[where] = chain
            direct.add(where)
            continue
        refusal = listen_refusal(classes)
        if refusal and "listen" in chain[0]["usage"]:
            raise BindError(f"Application {chain[0]['row']!r}: usage listen: {refusal}")
        raw[where] = chain
    return raw, direct


def _check_agent_keyed_row(agent_id: str, row: dict[str, Any], entries: dict[str, dict[str, Any]]) -> None:
    """Refuse an agent-keyed Application row (the released ``--connect`` / ``--listen`` path) that cannot be used."""
    if not str(row.get("protocol") or row.get("kind") or "").strip():
        raise BindError(
            f"Application {agent_id!r} is named by an agent but names no protocol; infra rows are "
            f"endpoints, so link the agent in the Flavour's bind.agents.{agent_id} instead"
        )
    if not str(row.get("url") or "").strip():
        raise BindError(f"Application {agent_id!r}: an agent-keyed row needs url, where the agent is reached")
    if agent_id in entries:
        raise BindError(
            f"Application {agent_id!r} is an agent-keyed row, so bind.agents.{agent_id} cannot also link it; "
            "drop one of the two"
        )


def _check_agent_on_chain(agent_id: str, chain: list[dict[str, Any]], usage: list[str]) -> None:
    """Refuse a chain the agent cannot ride: a layer that cannot name the id, or a usage beyond a row's capability."""
    from mas.library.standard.lib.stack.handle import agent_id_refusal, layer_classes

    for layer, cls in zip(chain, layer_classes(chain), strict=True):
        reason = agent_id_refusal(cls, layer["params"], agent_id)
        if reason:
            raise BindError(
                f"Flavour bind: agent {agent_id!r} cannot ride row {layer['row']!r} (layer {layer['type']!r}): {reason}"
            )
    for layer in chain:
        missing = [mode for mode in usage if mode not in layer["usage"]]
        if missing:
            raise BindError(
                f"Flavour bind.agents.{agent_id}: usage {missing[0]} is not a capability of row "
                f"{layer['row']!r} (its usage is {layer['usage']})"
            )


def _refuse_shared_bottoms(agent_chains: dict[str, list[dict[str, Any]]]) -> None:
    """Refuse two different chains whose bottom row is the same endpoint with the same params."""
    bottoms: dict[str, str] = {}
    for agent_id, chain in agent_chains.items():
        bottom = json.dumps([chain[0]["type"], chain[0]["params"]], sort_keys=True, default=str)
        other = bottoms.get(bottom)
        if other is not None and other != chain[0]["chain"]:
            raise BindError(
                f"Flavour bind.agents.{agent_id}: its bottom row {chain[0]['row']!r} is also the bottom of another "
                "chain with the same params; an agent on both would join it twice, so give each chain its own "
                "bottom row"
            )
        bottoms[bottom] = chain[0]["chain"]


def bind_flavour(
    resolved: ResolvedInfra, flavour_spec: dict[str, Any] | None, *, agent_ids: list[str] | None = None
) -> ResolvedInfra:
    """``resolved`` with the Flavour's chains taken out of its Application rows and given to its agents.

    Every agent of ``agent_ids`` that has no agent-keyed Application row of its
    own (the released ``--connect`` / ``--listen`` rows) rides its chain:
    ``bind.agents.<id>.agent_comm``, else ``bind.agent_comm``; with neither it
    keeps the in-process link. Its usage is ``bind.agents.<id>.usage``, else
    ``bind.usage``, else ``connect``. ``listen`` is opt-in: an agent with no
    usage is reached, not hosted. Every layer of the chain must accept the agent id
    (``agent_id_refusal``) whether or not its row uses ``{agent}``. A row
    resolves to an ``agent_comm`` link, else a ``layer``
    (``resolve_layer_class``); a one-row agent chain whose row resolves to a direct link
    (``is_direct_link``: the A2A SDK client at a url) becomes that agent's
    released agent-keyed row.
    """
    default_names, default_usage, entries = bound_chains(flavour_spec)
    rows = dict(resolved.applications or {})
    bind = flavour_spec.get("bind") if isinstance(flavour_spec, dict) else None
    if isinstance(bind, dict):
        _named_side_rows(bind, "events", rows=rows)
    known = list(agent_ids) if agent_ids is not None else list(entries)
    unknown = sorted(set(entries) - set(known))
    if unknown:
        raise BindError(f"Flavour bind.agents names {unknown[0]!r}, which is no agent of this session")
    wheres = {"bind.agent_comm": default_names}
    wheres.update({f"bind.agents.{a}.agent_comm": e["agent_comm"] for a, e in entries.items() if e["agent_comm"]})
    raw, direct = _planned_chains(wheres, rows, known)
    for chain in raw.values():
        for layer in chain:
            rows.pop(layer["row"], None)
    default = raw.get("bind.agent_comm", [])
    agent_chains: dict[str, list[dict[str, Any]]] = {}
    agent_usage: dict[str, list[str]] = {}
    for agent_id in known:
        entry = entries.get(agent_id) or {}
        usage = entry.get("usage") or list(default_usage)
        where = f"bind.agents.{agent_id}.agent_comm"
        if agent_id in rows:
            _check_agent_keyed_row(agent_id, rows[agent_id], entries)
            continue  # the released direct path, untouched
        chain = raw.get(where) or default
        if not chain:
            if entry.get("usage") or entry.get("params"):
                raise BindError(f"Flavour bind.agents.{agent_id} has usage or params but no chain: set agent_comm")
            continue
        chain = _for_agent(chain, agent_id, entry.get("params") or {}, f"bind.agents.{agent_id}")
        _check_agent_on_chain(agent_id, chain, usage)
        if where in direct:
            link = chain[0]
            if not str(link["params"].get("url") or "").strip():
                raise BindError(f"Application {link['row']!r}: {where} reaches the agent at this row, so it needs url")
            rows[agent_id] = {"protocol": link["type"], **link["params"], "usage": usage}
            continue
        agent_chains[agent_id] = chain
        agent_usage[agent_id] = usage
    _refuse_shared_bottoms(agent_chains)
    if (flavour_spec or {}).get("require_usage"):
        missing = [agent_id for agent_id in known if agent_id not in rows and agent_id not in agent_chains]
        if missing:
            raise BindError(f"require_usage: agent {missing[0]!r} has no bind chain or agent-keyed Application row")
    return dataclasses.replace(resolved, applications=rows, agent_chains=agent_chains, agent_usage=agent_usage)


def bind_session(resolved: ResolvedInfra | None, flavour: Any, *, agent_ids: list[str]) -> ResolvedInfra:
    """Apply a Flavour (a document or its ``spec``) to a session's infra: the one call every entry point makes."""
    return bind_flavour(resolved or ResolvedInfra(), flavour_spec_of(flavour), agent_ids=agent_ids)
