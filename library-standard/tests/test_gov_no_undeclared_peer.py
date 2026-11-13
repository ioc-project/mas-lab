#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""gov_no_undeclared_peer — leftover send dest not in delegates_to is BLOCK."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.library.standard.plugins.governance.no_undeclared_peer import (
    PLUGIN_ID,
    NoUndeclaredPeerPlugin,
    allowed_peers,
    send_dest,
    undeclared_peer_observation,
)
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision


def _spec(*delegates: str) -> dict:
    return {
        "workflow": {
            "entry": "mod",
            "nodes": [
                {"id": "mod", "delegates_to": list(delegates)},
                {"id": "worker"},
            ],
        }
    }


def _config(*delegates: str, agent_id: str = "mod") -> KernelConfig:
    return KernelConfig(agent_id=agent_id, agent_spec=_spec(*delegates))


def _intent(tool: str, **fields: object) -> EgressIntentView:
    return EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name=tool,
        **fields,
    )


def test_blocks_leftover_tool_to_dest_not_in_delegates_to() -> None:
    plugin = NoUndeclaredPeerPlugin()
    decision, name, reason = plugin.evaluate_egress(
        _intent("delegate_to_stranger"),
        config=_config("worker"),
    )
    assert decision == GovDecision.BLOCK
    assert name == PLUGIN_ID
    assert "stranger" in reason
    assert "worker" in reason
    assert "Do not send to it" in reason


def test_allows_leftover_tool_to_listed_dest() -> None:
    plugin = NoUndeclaredPeerPlugin()
    decision, _, reason = plugin.evaluate_egress(
        _intent("delegate_to_worker"),
        config=_config("worker"),
    )
    assert decision == GovDecision.ALLOW
    assert "worker" in reason


def test_empty_delegates_to_blocks_leftover_send() -> None:
    plugin = NoUndeclaredPeerPlugin()
    decision, _, reason = plugin.evaluate_egress(
        _intent("delegate_to_worker"),
        config=_config(),
    )
    assert decision == GovDecision.BLOCK
    assert "(none)" in reason


def test_passes_ordinary_tool_names() -> None:
    plugin = NoUndeclaredPeerPlugin()
    decision, _, reason = plugin.evaluate_egress(
        _intent("get_metrics"),
        config=_config("worker"),
    )
    assert decision == GovDecision.ALLOW
    assert "not leftover send" in reason


def test_passes_when_not_a_tool_call() -> None:
    plugin = NoUndeclaredPeerPlugin()
    intent = EgressIntentView(
        op="LLM_CALL",
        destructive=False,
        correlation_id=1,
        tool_name="delegate_to_stranger",
    )
    decision, _, reason = plugin.evaluate_egress(intent, config=_config("worker"))
    assert decision == GovDecision.ALLOW
    assert "not a tool call" in reason


def test_a_user_tool_named_send_is_not_the_send_path() -> None:
    decision, _, _ = NoUndeclaredPeerPlugin().evaluate_egress(
        _intent("send", tool_arguments={"target": "stranger@example.com", "message": "mail"}),
        config=_config("worker"),
    )
    assert decision == GovDecision.ALLOW


def test_a_declared_user_tool_named_delegate_to_x_is_left_alone() -> None:
    config = KernelConfig(agent_id="mod", agent_spec={**_spec("worker"), "tools": ["delegate_to_stranger"]})
    decision, _, _ = NoUndeclaredPeerPlugin().evaluate_egress(_intent("delegate_to_stranger"), config=config)
    assert decision == GovDecision.ALLOW


def test_send_dest_and_allowed_peers_helpers() -> None:
    assert send_dest(_intent("delegate_to_worker")) == "worker"
    assert send_dest(_intent("get_metrics")) is None
    assert send_dest(_intent("send", tool_arguments={"target": "worker"})) is None
    assert allowed_peers(_config("worker")) == ["worker"]
    assert allowed_peers(_config()) == []
    assert allowed_peers(KernelConfig(agent_id="mod", agent_spec="nope")) == []


def test_observation_text() -> None:
    text = undeclared_peer_observation("stranger", ["worker"])
    assert text == ("Peer 'stranger' is not in this agent's delegates_to. Do not send to it. Allowed peers: worker.")


def test_with_hardened_names_the_plugin() -> None:
    overlay_path = (
        Path(__file__).resolve().parents[1] / "src" / "mas" / "library" / "standard" / "controls" / "with-hardened.yaml"
    )
    doc = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    names = [g if isinstance(g, str) else next(iter(g)) for g in doc["spec"]["governance"]]
    assert "gov_no_undeclared_peer" in names
    assert "gov_no_undeclared_tool" in names


def test_a_kernel_without_an_agent_id_has_no_lock() -> None:
    config = _config("worker", agent_id="")
    assert allowed_peers(config) is None
    decision, _plugin, _reason = NoUndeclaredPeerPlugin().evaluate_egress(_intent("delegate_to_anyone"), config=config)
    assert decision == GovDecision.ALLOW
