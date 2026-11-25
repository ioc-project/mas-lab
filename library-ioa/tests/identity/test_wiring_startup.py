#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A record whose secret cannot be read stops the start with one event; a started layer is a holder the gate finds."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mas.library.standard.lib.identity import holders
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from mas.runtime.secrets import SecretRef


def _layer(params: dict[str, Any], **extra: Any) -> IdentityLayer:
    return IdentityLayer(identity="id1", scheme="wiring_canary", params=params, **extra)


def test_a_missing_secret_stops_the_start_with_one_startup_event(events: list[dict[str, Any]]) -> None:
    """Kills: a start that goes on without its key (and fails on the first call), or two events for one record."""
    with pytest.raises(AgentCommError, match="refused:missing_secret"):
        _layer({"key": SecretRef(name="not-in-the-store")}, agent_id="desk")
    startup = [e for e in events if e["kind"] == "identity.startup"]
    assert len(startup) == 1
    assert (startup[0]["outcome"], startup[0]["code"], startup[0]["record"], startup[0]["scheme"]) == (
        "denied",
        "refused:missing_secret",
        "id1",
        "wiring_canary",
    )
    assert startup[0]["actor"] == "desk"
    assert "not-in-the-store" not in json.dumps(events)  # the event names the record, never the secret


def test_an_optional_secret_may_be_absent(events: list[dict[str, Any]]) -> None:
    """Kills: an optional reference that stops the start."""
    layer = _layer({"key": SecretRef(name="not-in-the-store", optional=True)})
    layer.stop()
    assert not [e for e in events if e["kind"] == "identity.startup"]


def test_a_started_layer_is_held_by_its_agent_until_it_stops() -> None:
    """Kills: a gate that cannot find the wallet of the agent it judges, or one that outlives its layer."""
    from wiring_builders import key_ref

    holders.reset()  # a layer another test left unstopped is not this agent's
    layer = _layer({"key": key_ref("a-key")}, agent_id="desk")
    assert holders.records_of("desk") == ["id1"]
    assert holders.wallets("desk", "id1") == [layer.caller]
    layer.stop()
    assert holders.records_of("desk") == []
