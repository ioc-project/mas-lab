#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Value-free events at the identity boundary: one small vocabulary and the rulebook every event must pass.

No new event kind is added to the kernel. The caller and the edge hooks publish plain dicts with ``publish_event`` (the
process event stream a pipeline or a UI subscribes to) and ``emit_to_channel``. They are ordinary MAS Lab events:
nothing here depends on OpenTelemetry.

Kinds
=====

* ``identity.present``: a credential was asked of the wallet for a request (``outcome`` ``ok`` or ``denied``);
* ``identity.handshake``: an in-session handshake (``ok`` or ``denied``);
* ``identity.verify``: an inbound credential was checked (``ok``, ``challenged`` or ``denied``);
* ``identity.challenge``: a challenge was ``issued`` by a verifier or ``answered`` by a holder;
* ``identity.revoke``: an identity of a record was revoked (by the host's order, or because the wallet said so);
* ``identity.gate``: the identity gate decided a call (``ok`` allowed, ``denied`` blocked, ``hold`` waits for a human);
* ``identity.startup``: a record could not start because a secret it names cannot be read (``denied``,
  ``refused:missing_secret``): one event per record, before the session exits.

A refusal is an event of the kind that was refused, with ``outcome`` ``denied`` and a closed ``code``.

Fields
======

The set is closed (:data:`FIELDS`): an emitter cannot add one, and an unknown name is dropped.

* on every event: ``kind``, ``source`` (``identity``), ``ts`` (UTC epoch milliseconds) and ``event_id``;
* who and where: ``actor`` (the acting agent), ``record``, ``identity`` (the wallet's identity id), ``scheme``,
  ``protocol``, ``host`` (host and port, nothing else of a URL), ``operation`` (the method or ``tools/call:<tool>``),
  ``call`` (the kernel's correlation id of the tool call in flight, when there is one);
* how it went: ``phase``, ``direction``, ``outcome``, ``code`` (a value-free code), ``latency_ms``, ``grant`` (the
  summary the contract defines: scopes, audience, valid_until, depth; never a token).

What is never in an event: evidence, headers, metadata, claims, a subject or session value, a nonce, a URL's path or
query, message text. A subject or session value is not in an event even as a digest: an event names the actor, the
record and the code. :func:`validate_event` is the rulebook the tests hold every published event to. It refuses; it
does not scan for secrets. When no identity is configured nothing in this module runs, so nothing is published.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from mas.library.standard.lib.identity.contract import GRANT_AXES, MAX_GRANT_SCOPES, Grant
from mas.runtime.boundary.obs.event_stream import emit_to_channel, publish_event

KINDS = (
    "identity.present",
    "identity.handshake",
    "identity.verify",
    "identity.challenge",
    "identity.revoke",
    "identity.gate",
    "identity.startup",
)
OUTCOMES = frozenset({"ok", "denied", "challenged", "hold"})
DIRECTIONS = frozenset({"issued", "answered"})
PHASES = frozenset({"initiate", "challenge", "sign"})

FIELDS = frozenset(
    {
        "actor",
        "record",
        "identity",
        "scheme",
        "protocol",
        "host",
        "operation",
        "call",
        "phase",
        "direction",
        "outcome",
        "code",
        "latency_ms",
        "grant",
    }
)
COMMON = ("kind", "source", "ts", "event_id")

TOOL_OPERATION = "tools/call:"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@#-]{0,127}")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?")
_PROTOCOL = re.compile(r"[a-z0-9][a-z0-9_.+-]{0,31}")
_ID_FIELDS = ("actor", "record", "identity", "scheme", "call")
_MAX_SUMMARY_SCOPES = min(MAX_GRANT_SCOPES, 16)
_MAX_SUMMARY_SCOPE = 64


class EventError(ValueError):
    """An event broke the rulebook. The message names the field, never the value."""


def _id(value: Any) -> str:
    """``value`` as an id: characters outside the grammar become ``_``, cut at 128; ``""`` when nothing is left."""
    text = re.sub(r"[^A-Za-z0-9_.:/@#-]", "_", str(value))[:128]
    return text if _ID.fullmatch(text) else ""


def host_of(target: str) -> str:
    """The host (and port) of ``target``, dropping credentials, path and query; ``""`` for a name that is not one."""
    parts = urlsplit(target)
    if parts.hostname:
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        text = f"{host}:{parts.port}" if parts.port else host
    else:
        text = target.split("/", 1)[0]
    return text if _HOST.fullmatch(text) else ""


def grant_summary(grant: Grant | None) -> dict[str, Any]:
    """The part of a grant an event may carry: the four contract axes, bounded."""
    if not grant:
        return {}
    out: dict[str, Any] = {}
    if grant.get("scopes"):
        out["scopes"] = [_id(s)[:_MAX_SUMMARY_SCOPE] for s in grant["scopes"][:_MAX_SUMMARY_SCOPES] if _id(s)]
    if grant.get("audience"):
        out["audience"] = host_of(grant["audience"])
    if isinstance(grant.get("valid_until"), (int, float)):
        out["valid_until"] = float(grant["valid_until"])
    if isinstance(grant.get("depth"), int):
        out["depth"] = grant["depth"]
    return {k: v for k, v in out.items() if v not in ("", [], None)}


def _call_in_flight() -> tuple[str, str]:
    """The operation and the kernel's correlation id of the tool call in flight in this context, else ``("", "")``."""
    from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL

    flight = IN_FLIGHT_TOOL.get()
    if flight is None or not flight.name:
        return "", ""
    return f"{TOOL_OPERATION}{flight.name}", str(flight.call) if flight.call else ""


_OPERATION_FIELD = re.compile(r"[^A-Za-z0-9_.:/@#<>= -]")


def _clean(name: str, value: Any) -> Any:
    """A field as an event may carry it. Ids, the operation and the host can hold what a peer chose (an A2A skill
    id): they are cut to the rulebook's grammar and size here, so an event is well formed whatever was passed."""
    if name in _ID_FIELDS:
        return _id(value)
    if name == "operation":
        return _OPERATION_FIELD.sub("_", str(value))[:192]
    if name == "host":
        return host_of(str(value))
    if name == "code":
        text = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value))[:64]
        return text if _CODE.fullmatch(text) else ""
    return value


def emit(kind: str, *, grant: Grant | None = None, **fields: Any) -> dict[str, Any]:
    """Publish one event and return it. An unknown field is dropped and an empty one omitted, so a typo cannot leak.

    A crossing made while a tool call is in flight belongs to that call: its operation and the kernel's correlation id
    win over the ``operation`` the emitter gave.
    """
    operation, call = _call_in_flight()
    event: dict[str, Any] = {
        "kind": kind,
        "source": "identity",
        "ts": int(time.time() * 1000),
        "event_id": uuid.uuid4().hex,
    }
    if operation:
        fields["operation"] = operation
    if call:
        fields["call"] = call
    event.update({k: _clean(k, v) for k, v in fields.items() if k in FIELDS and v not in ("", None)})
    for name in [k for k, v in event.items() if v in ("", None)]:  # a value nothing of which was allowed is omitted
        del event[name]
    summary = grant_summary(grant)
    if summary:
        event["grant"] = summary
    publish_event(event)
    emit_to_channel(event)
    return event


def validate_event(event: Mapping[str, Any]) -> None:
    """The rulebook: raise :class:`EventError` unless ``event`` is a well-formed, value-free identity event."""
    if event.get("kind") not in KINDS:
        raise EventError("kind")
    if event.get("source") != "identity":
        raise EventError("source")
    if not isinstance(event.get("ts"), int) or not _ID.fullmatch(str(event.get("event_id", ""))):
        raise EventError("ts or event_id")
    for name in event:
        if name not in FIELDS and name not in COMMON:
            raise EventError(f"field {name}")
    for name in _ID_FIELDS:
        if name in event and not (isinstance(event[name], str) and _ID.fullmatch(event[name])):
            raise EventError(name)
    if "operation" in event and not (isinstance(event["operation"], str) and len(event["operation"]) <= 192):
        raise EventError("operation")
    if "protocol" in event and not _PROTOCOL.fullmatch(str(event["protocol"])):
        raise EventError("protocol")
    if "host" in event and not _HOST.fullmatch(str(event["host"])):
        raise EventError("host")
    if "code" in event and not _CODE.fullmatch(str(event["code"])):
        raise EventError("code")
    if "outcome" in event and event["outcome"] not in OUTCOMES:
        raise EventError("outcome")
    if "direction" in event and event["direction"] not in DIRECTIONS:
        raise EventError("direction")
    if "phase" in event and event["phase"] not in PHASES:
        raise EventError("phase")
    if "latency_ms" in event and not isinstance(event["latency_ms"], (int, float)):
        raise EventError("latency_ms")
    grant = event.get("grant")
    if grant is not None:
        if not isinstance(grant, Mapping) or not set(grant) <= GRANT_AXES:
            raise EventError("grant")
