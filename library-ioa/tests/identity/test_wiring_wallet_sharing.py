#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One wallet per bind, record and agent: a revocation reaches every layer, and layers do not leak listeners."""

from __future__ import annotations

import gc

import pytest
from mas.library.standard.lib.identity import holders
from mas.library.standard.plugins.identity import layer as layer_module
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from wiring_builders import key_ref

PARAMS = {"key": key_ref("k" * 16), "subject": "alice"}


def layer(agent: str = "desk", scope: str = "agent", holder_scope: str = "run-a", **extra: object) -> IdentityLayer:
    return IdentityLayer(
        identity="id1",
        scheme="signed_badge",
        params=PARAMS,
        agent_id=agent,
        scope=scope,
        holder_scope=holder_scope,
        **extra,
    )


@pytest.fixture(autouse=True)
def _clean():
    holders.reset()
    layer_module.reset_shared()
    yield
    holders.reset()
    layer_module.reset_shared()


def test_two_layers_of_one_agent_and_record_share_one_wallet_so_one_revocation_reaches_both() -> None:
    """Kills: one wallet per layer, where a revocation reaches one layer and the gate allows on the other's wallet."""
    first, second = layer(), layer()
    assert first.caller is second.caller
    assert holders.wallets("desk", "id1", "run-a") == [first.caller]
    told: list[str] = []
    first.caller.on_revoked(told.append)
    second.caller.notify_revoked("badge")
    assert told == ["badge"] and first.caller.is_revoked("badge")


def test_another_agent_or_another_bind_gets_its_own_wallet() -> None:
    """Kills: a wallet shared across agents (scope agent) or across runs of one MAS."""
    mine = layer()
    assert layer(agent="ops").caller is not mine.caller
    assert layer(holder_scope="run-b").caller is not mine.caller
    assert layer(scope="process", agent="ops").caller is layer(scope="process", agent="other").caller


def test_a_layer_that_is_collected_without_stop_leaves_no_listener_behind() -> None:
    """Kills: listeners accumulating on a shared wallet by layers that were collected without stop."""
    keeper = layer()
    caller = keeper.caller
    baseline = len(caller._listeners)
    layer()
    gc.collect()
    assert len(caller._listeners) == baseline
    keeper.stop()
    assert holders.wallets("desk", "id1", "run-a") == [caller]  # the collected layer's transport may still use it


def test_a_wallet_stays_held_while_a_transport_presents_with_it_after_its_layer_is_collected() -> None:
    """Kills: the gate losing the wallet of a live transport because the layer that built it was collected."""
    built = layer()
    in_use = built.caller  # what the transport's auth keeps
    del built
    gc.collect()
    assert holders.wallets("desk", "id1", "run-a") == [in_use]
    assert layer().caller is in_use
    del in_use
    gc.collect()
    assert holders.wallets("desk", "id1", "run-a") == []


def test_a_parameter_the_layer_does_not_know_is_refused_not_swallowed() -> None:
    """Kills: a typo (`digest_key`, `audiance`) accepted silently by a layer that then runs without it."""
    with pytest.raises(AgentCommError, match="unknown parameter 'digest_key'"):
        layer(digest_key=b"k")


def test_a_process_wallet_belongs_to_no_agent_so_no_agents_tools_can_manage_it() -> None:
    """Kills: a process-wide wallet registered under every agent that uses it, revocable by any one agent's model."""
    shared = layer(agent="desk", scope="process")
    layer(agent="ops", scope="process")
    assert holders.wallets("desk", "id1", "run-a") == [] and holders.wallets("ops", "id1", "run-a") == []
    assert holders.wallets("", "id1", "run-a") == [shared.caller]
