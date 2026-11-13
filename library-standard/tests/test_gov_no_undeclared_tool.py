#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""gov_no_undeclared_tool — names not in this LLM call's tools list are BLOCK."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from mas.library.standard.plugins.governance.no_undeclared_tool import (
    PLUGIN_ID,
    NoUndeclaredToolPlugin,
    allowed_tool_names,
    undeclared_tool_observation,
)
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision


def _intent(tool: str, offered: tuple[str, ...] | None) -> EgressIntentView:
    return EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name=tool,
        offered_tools=offered,
    )


def test_blocks_name_not_in_offered_list_even_if_spec_has_it() -> None:
    plugin = NoUndeclaredToolPlugin()
    config = KernelConfig(agent_spec={"tools": ["get_metrics", "get_deployments"]})
    decision, name, reason = plugin.evaluate_egress(
        _intent("get_deployments", ("get_metrics", "get_logs")),
        config=config,
    )
    assert decision == GovDecision.BLOCK
    assert name == PLUGIN_ID
    assert "get_deployments" in reason
    assert "get_metrics" in reason
    assert "Do not call it" in reason


def test_allows_offered_name() -> None:
    plugin = NoUndeclaredToolPlugin()
    decision, _, _ = plugin.evaluate_egress(
        _intent("get_metrics", ("get_metrics", "get_logs")),
        config=KernelConfig(),
    )
    assert decision == GovDecision.ALLOW


def test_falls_back_to_spec_tools_when_offer_was_not_recorded() -> None:
    plugin = NoUndeclaredToolPlugin()
    config = KernelConfig(agent_spec={"tools": ["get_metrics"]})
    blocked, _, _ = plugin.evaluate_egress(_intent("get_deployments", None), config=config)
    allowed, _, _ = plugin.evaluate_egress(_intent("get_metrics", None), config=config)
    assert blocked == GovDecision.BLOCK
    assert allowed == GovDecision.ALLOW


def test_unknown_offer_does_not_block() -> None:
    plugin = NoUndeclaredToolPlugin()
    decision, _, reason = plugin.evaluate_egress(
        _intent("get_deployments", None),
        config=KernelConfig(),
    )
    assert decision == GovDecision.ALLOW
    assert "no offered-tool list" in reason


def test_empty_offer_blocks_every_tool_call() -> None:
    plugin = NoUndeclaredToolPlugin()
    decision, _, reason = plugin.evaluate_egress(
        _intent("get_metrics", ()),
        config=KernelConfig(agent_spec={"tools": ["get_metrics"]}),
    )
    assert decision == GovDecision.BLOCK
    assert "(none)" in reason


def test_allowed_tool_names_prefers_offered_over_spec() -> None:
    intent = _intent("x", ("get_metrics",))
    config = KernelConfig(agent_spec={"tools": ["get_metrics", "get_deployments"]})
    assert allowed_tool_names(intent, config=config) == ["get_metrics"]


def test_observation_text() -> None:
    text = undeclared_tool_observation("get_deployments", ["get_metrics", "get_logs"])
    assert text == (
        "Tool 'get_deployments' was not in the tools list offered to you. "
        "Do not call it. Available tools: get_metrics, get_logs."
    )


def test_with_hardened_overlay_appends_the_plugin() -> None:
    overlay_path = (
        Path(__file__).resolve().parents[1] / "src" / "mas" / "library" / "standard" / "controls" / "with-hardened.yaml"
    )
    doc = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    names = [g if isinstance(g, str) else next(iter(g)) for g in doc["spec"]["governance"]]
    assert "gov_no_undeclared_tool" in names


def test_spec_tools_as_dicts_are_resolved_by_name() -> None:
    plugin = NoUndeclaredToolPlugin()
    config = KernelConfig(agent_spec={"tools": [{"name": "get_metrics"}, {"id": "get_logs"}]})
    blocked, _, _ = plugin.evaluate_egress(_intent("get_deployments", None), config=config)
    allowed, _, _ = plugin.evaluate_egress(_intent("get_logs", None), config=config)
    assert blocked == GovDecision.BLOCK
    assert allowed == GovDecision.ALLOW


def test_empty_tool_name_is_not_checked() -> None:
    plugin = NoUndeclaredToolPlugin()
    decision, _, reason = plugin.evaluate_egress(
        _intent("", ("get_metrics",)),
        config=KernelConfig(),
    )
    assert decision == GovDecision.ALLOW
    assert "not a tool call" in reason


def test_non_tool_ops_are_not_checked() -> None:
    plugin = NoUndeclaredToolPlugin()
    for op in ("LLM_CALL", "STOP", "MEMORY_OP"):
        intent = EgressIntentView(
            op=op,
            destructive=False,
            correlation_id=1,
            tool_name="get_deployments",
            offered_tools=(),
        )
        decision, _, _ = plugin.evaluate_egress(intent, config=KernelConfig())
        assert decision == GovDecision.ALLOW


SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "governance" / "undeclared-tool"


def _mwe() -> dict:
    return json.loads((SAMPLE / "llm_call.json").read_text(encoding="utf-8"))


def _offered_from_mwe(doc: dict) -> list[str]:
    return [t["function"]["name"] for t in doc["tools"]]


def test_mwe_sample_files_exist() -> None:
    assert (SAMPLE / "llm_call.json").is_file()
    assert (SAMPLE / "agent.yaml").is_file()
    assert (SAMPLE / "skills" / "data-access-protocol" / "SKILL.md").is_file()
    assert (SAMPLE / "README.md").is_file()
    for name in ("get_metrics", "get_logs", "get_service_health"):
        assert (SAMPLE / "tools" / f"{name}.tool.yaml").is_file()
    assert not (SAMPLE / "tools" / "get_deployments.tool.yaml").is_file()
    readme = (SAMPLE / "README.md").read_text()
    assert "scripts/repro_gov_no_undeclared_tool" not in readme
    assert "library-standard/examples/governance/undeclared-tool" in readme
    assert "library-samples/apps" not in readme
    assert "plugins/governance/samples" not in readme


def test_mwe_tools_omit_get_deployments_but_prompt_names_it() -> None:
    doc = _mwe()
    offered = _offered_from_mwe(doc)
    assert offered == ["get_metrics", "get_logs", "get_service_health"]
    assert "get_deployments" not in offered
    blob = json.dumps(doc["messages"])
    assert "get_deployments" in blob
    skill = (SAMPLE / "skills" / "data-access-protocol" / "SKILL.md").read_text(encoding="utf-8")
    assert "get_deployments" in skill


def test_mwe_agent_spec_matches_offered_tools() -> None:
    doc = _mwe()
    agent = yaml.safe_load((SAMPLE / "agent.yaml").read_text(encoding="utf-8"))
    names = []
    for entry in agent["spec"]["tools"]:
        if isinstance(entry, dict) and entry.get("ref"):
            names.append(Path(str(entry["ref"])).name.replace(".tool.yaml", ""))
        else:
            names.append(str(entry))
    assert names == _offered_from_mwe(doc)
    assert "data-access-protocol" in agent["spec"]["skills"]
    assert "get_deployments" not in names
    assert "governance" not in agent["spec"]
    control = yaml.safe_load((SAMPLE / "control.yaml").read_text(encoding="utf-8"))
    assert "gov_no_undeclared_tool" in control["spec"]["governance"]
    flavour = yaml.safe_load((SAMPLE / "flavour.yaml").read_text(encoding="utf-8"))
    assert flavour["spec"]["control"] == [{"ref": "./control.yaml"}]


def test_sample_agent_validates() -> None:
    pytest.importorskip("jsonschema")
    from mas.ctl.validate import validate_file

    result = validate_file(SAMPLE / "agent.yaml", kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_plugin_blocks_mwe_get_deployments() -> None:
    offered = tuple(_offered_from_mwe(_mwe()))
    decision, name, reason = NoUndeclaredToolPlugin().evaluate_egress(
        _intent("get_deployments", offered),
        config=KernelConfig(agent_spec={"tools": list(offered) + ["get_deployments"]}),
    )
    assert decision == GovDecision.BLOCK
    assert name == PLUGIN_ID
    assert "get_deployments" in reason
    assert "get_metrics" in reason
    assert "get_logs" in reason
    assert "get_service_health" in reason


def test_plugin_allows_mwe_listed_tools() -> None:
    offered = tuple(_offered_from_mwe(_mwe()))
    plugin = NoUndeclaredToolPlugin()
    for name in offered:
        decision, _, _ = plugin.evaluate_egress(_intent(name, offered), config=KernelConfig())
        assert decision == GovDecision.ALLOW


def test_a_pattern_sending_to_a_declared_peer_is_not_blocked_by_spec_tools() -> None:
    from mas.runtime.boundary.gov.policy import EgressIntentView
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.kernel.coupling import GovDecision

    spec = {"tools": ["calc"], "workflow": {"nodes": [{"id": "a", "delegates_to": ["b"]}, {"id": "b"}]}}
    config = KernelConfig(agent_spec=spec, agent_id="a")

    def verdict(tool: str):
        intent = EgressIntentView(op="TOOL_CALL", destructive=False, correlation_id=1, tool_name=tool)
        return NoUndeclaredToolPlugin().evaluate_egress(intent, config=config)[0]

    assert verdict("delegate_to_b") == GovDecision.ALLOW
    assert verdict("delegate_to_c") == GovDecision.BLOCK
