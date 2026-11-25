#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What an identity event carries: a timestamp, a unique id, the call the runtime gave, the actor, the grant's summary
and the operation; and that none of it is a value (no subject, session or chain, not even as a digest)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mas.library.standard.lib.identity import observe
from mas.library.standard.lib.identity.principal import bind
from mas.runtime.engine.tool_dispatch import IN_FLIGHT_TOOL, InFlightTool
from wiring_builders import a2a_stack, close, envelope, identity_step, mcp_provider
from wiring_schemes import Granting
from wiring_servers import FakeMcp, serve_a2a

AUD = "http://127.0.0.1:9"


def _of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["kind"] == kind]


async def _present(events: list[dict[str, Any]], **layer: Any) -> dict[str, Any]:
    """One A2A send under a granting scheme; the ``identity.present`` event it published."""
    with serve_a2a(lambda p, **_: {"text": p}) as peer:
        stack, link, _ = a2a_stack(peer.url, "wiring_granting", agent_id="agent-a", **layer)
        try:
            with bind({"actor": "agent-a", "subject": "alice", "session": "sess-1"}):
                await stack.asend(envelope("hi"))
        finally:
            link.stop()
    return _of(events, "identity.present")[-1]


async def test_with_no_key_there_is_no_digest_at_all(events: list[dict[str, Any]]) -> None:
    """Kills: a fallback to an unkeyed hash of a guessable id."""
    event = await _present(events)
    assert not {"subject", "session", "chain"} & set(event)
    assert event["actor"] == "agent-a"


async def test_the_grant_is_summarised_and_never_a_token(events: list[dict[str, Any]]) -> None:
    """Kills: a grant summary that leaks the audience's path or query, or drops an axis."""
    event = await _present(events)
    assert event["grant"] == {
        "scopes": ["rail", "pay:eur"],
        "audience": "pay.example:8443",
        "valid_until": Granting.GRANT["valid_until"],
        "depth": 1,
    }
    assert "token=x" not in json.dumps(event) and "Bearer" not in json.dumps(event)


async def test_the_call_id_is_the_kernels_correlation_id_of_the_tool_call(events: list[dict[str, Any]]) -> None:
    """Kills: a call id invented here instead of the one the governance decision of the same call carries."""
    with FakeMcp() as server:
        provider = mcp_provider(server.url, identity_step("wiring_granting"))
        token = IN_FLIGHT_TOOL.set(InFlightTool(owner=1, name="pay_fare", arguments={}, call=41))
        try:
            with bind({"actor": "agent-a"}):
                await provider.acall_tool("echo", {})
        finally:
            IN_FLIGHT_TOOL.reset(token)
            await close(provider)
    presents = _of(events, "identity.present")
    ours = [e for e in presents if e.get("call")]
    assert ours and {e["call"] for e in ours} == {"41"}
    assert {e["operation"] for e in ours} == {"tools/call:pay_fare"}  # the call in flight, whatever the method
    # the session's own setup belongs to no tool call: it is not stamped with the call that happened to open it
    assert {e["operation"] for e in presents if not e.get("call")} <= {"initialize", "notifications/initialized"}


@pytest.mark.parametrize(
    ("target", "host"),
    [
        ("https://user:pw@pay.example:8443/mcp/x?token=1#f", "pay.example:8443"),
        ("http://127.0.0.1:9/mcp", "127.0.0.1:9"),
        ("http://[::1]:7/mcp", "[::1]:7"),
        ("bob", "bob"),
        ("acme/lab/wiring", "acme"),
        ("a b?c", ""),
    ],
)
def test_host_is_host_and_port_and_nothing_else(target: str, host: str) -> None:
    assert observe.host_of(target) == host
