#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance plugin chain + TOOL_CALL BLOCK fed back as a tool observation."""

from __future__ import annotations

import json
from pathlib import Path

from mas.library.standard.lib.observability.emit import JsonlFileEmitter
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.standard.plugins.governance.no_undeclared_tool import NoUndeclaredToolPlugin
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.runtime.boundary.gov.plugin import GovernancePluginChain
from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

REPO_ROOT = Path(__file__).resolve().parents[2]
HARDENED_CONTROL = REPO_ROOT / "library-standard/src/mas/library/standard/controls/with-hardened.yaml"


class _HitlAlways:
    def evaluate_egress(self, intent, *, config):
        return GovDecision.HITL, "hitl", "review every tool"


class _AllowAll:
    def evaluate_egress(self, intent, *, config):
        return GovDecision.ALLOW, "allow", "ok"


class _RecordCall:
    """Fails the test if the chain consults this plugin after a BLOCK."""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate_egress(self, intent, *, config):
        self.calls += 1
        return GovDecision.ALLOW, "later", "should not run after BLOCK"


def test_chain_block_exits_before_later_plugins() -> None:
    later = _RecordCall()
    chain = GovernancePluginChain([NoUndeclaredToolPlugin(), later])
    intent = EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name="get_deployments",
        offered_tools=("get_metrics",),
    )
    decision, name, reason = chain.evaluate_egress(intent, config=KernelConfig())
    assert decision == GovDecision.BLOCK
    assert name == "gov_no_undeclared_tool"
    assert "get_deployments" in reason
    assert later.calls == 0


def test_chain_pass_continues_to_next_plugin() -> None:
    later = _RecordCall()
    chain = GovernancePluginChain([_AllowAll(), later])
    intent = EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name="get_metrics",
        offered_tools=("get_metrics",),
    )
    decision, name, _ = chain.evaluate_egress(intent, config=KernelConfig())
    assert decision == GovDecision.ALLOW
    assert name == "later"
    assert later.calls == 1


def test_chain_block_beats_earlier_hitl_hold() -> None:
    chain = GovernancePluginChain([_HitlAlways(), NoUndeclaredToolPlugin()])
    intent = EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name="get_deployments",
        offered_tools=("get_metrics",),
    )
    decision, name, reason = chain.evaluate_egress(intent, config=KernelConfig())
    assert decision == GovDecision.BLOCK
    assert name == "gov_no_undeclared_tool"
    assert "get_deployments" in reason


def test_chain_all_pass() -> None:
    chain = GovernancePluginChain([_AllowAll(), NoUndeclaredToolPlugin()])
    intent = EgressIntentView(
        op="TOOL_CALL",
        destructive=False,
        correlation_id=1,
        tool_name="get_metrics",
        offered_tools=("get_metrics",),
    )
    decision, _, _ = chain.evaluate_egress(intent, config=KernelConfig())
    assert decision == GovDecision.ALLOW


def test_undeclared_tool_block_is_fed_back_not_fatal() -> None:
    """BLOCK on TOOL_CALL injects a tool observation and continues the LLM loop."""

    class _Engine(SimulatedEngine):
        def invoke(self, io):
            ret = super().invoke(io)
            if io.op == "LLM_CALL":
                return ret.model_copy(update={"offered_tools": ["get_metrics", "get_logs"]})
            return ret

    engine = _Engine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("get_deployments", {"service": "payment-service"}),
        stop_text="used listed tools only",
    )
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(egress_governance_plugin=NoUndeclaredToolPlugin()),
    )
    inst.capture_session_baseline()
    trace = inst.run_user_text("check latency")

    assert not any(getattr(err, "code", "") == "GOV_BLOCK" for err in trace.boundary_errors)
    gov_ex = [ex for ex in trace.exchanges if ex.kind == "gov_block"]
    assert gov_ex, f"expected AGENT->GOV exchange, got {[ex.kind for ex in trace.exchanges]!r}"
    assert any("gov_no_undeclared_tool" in (ex.policy_name or "") for ex in gov_ex)
    results = [ev.text for ev in inst.kernel.run.events if ev.response_kind == "TOOL_RESULT"]
    assert any("was not in the tools list offered to you" in (t or "") for t in results)
    assert any("get_metrics" in (t or "") for t in results)
    assert trace.client_responses
    assert "used listed tools only" in (trace.client_responses[-1].content or "")


def test_block_on_a_system_plugin_call_is_a_tool_observation() -> None:
    """BLOCK on a system-plugin tool name reaches the model as a tool observation."""

    class _Engine(SimulatedEngine):
        def invoke(self, io):
            ret = super().invoke(io)
            if io.op == "LLM_CALL":
                return ret.model_copy(update={"offered_tools": ["get_metrics"]})
            return ret

    engine = _Engine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("inform_user", {"message": "working"}),
        stop_text="noted",
    )
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(egress_governance_plugin=NoUndeclaredToolPlugin()),
    )
    inst.capture_session_baseline()
    trace = inst.run_user_text("status")
    results = [ev.text for ev in inst.kernel.run.events if ev.response_kind == "TOOL_RESULT"]
    assert any("was not in the tools list offered to you" in (t or "") for t in results)
    assert any("inform_user" in (t or "") for t in results)
    assert trace.client_responses


def test_build_kernel_config_wires_no_undeclared_tool() -> None:
    from mas.runtime.registry import get_registry, register_plugin
    from mas.runtime.spec.gov import build_kernel_config, parse_gov_spec

    urn = "mas.gov.no_undeclared_tool"
    if get_registry().resolve_by_type("governance", "gov_no_undeclared_tool") is None:
        register_plugin(
            urn,
            NoUndeclaredToolPlugin,
            shortcuts=["gov_no_undeclared_tool", "no_undeclared_tool"],
            attributes={"plugin_type": "governance"},
        )
    config = build_kernel_config(parse_gov_spec(["gov_no_undeclared_tool"]))
    assert isinstance(config.egress_governance_plugin, NoUndeclaredToolPlugin)

    aliased = build_kernel_config(parse_gov_spec(["no_undeclared_tool"]))
    assert isinstance(aliased.egress_governance_plugin, NoUndeclaredToolPlugin)


def test_with_hardened_control_does_not_copy_governance_onto_the_agent() -> None:
    from mas.ctl.session.flavour_control import (
        apply_overlay_controls_to_manifest,
        control_lists_for_agent,
        control_row_from_path,
    )

    control = HARDENED_CONTROL
    base = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "telemetry"},
        "spec": {"governance": ["sample_governance"], "tools": ["get_metrics", "get_logs"]},
    }
    merged = apply_overlay_controls_to_manifest(base, [control], agent_id="telemetry", entry_id="telemetry", warn=False)
    assert merged["spec"]["governance"] == ["sample_governance"]
    names = [
        g if isinstance(g, str) else next(iter(g))
        for g in control_lists_for_agent([control_row_from_path(control)], agent_id="telemetry", entry_id="telemetry")[
            "governance"
        ]
    ]
    assert "gov_no_undeclared_tool" in names
    assert "gov_no_undeclared_peer" in names


def test_undeclared_tool_block_is_in_native_events_jsonl(tmp_path: Path) -> None:
    """The sample agent scenario's BLOCK must show up in native events.jsonl."""

    class _Engine(SimulatedEngine):
        def invoke(self, io):
            ret = super().invoke(io)
            if io.op == "LLM_CALL":
                return ret.model_copy(update={"offered_tools": ["get_metrics", "get_logs"]})
            return ret

    events_path = tmp_path / "events.jsonl"
    engine = _Engine(
        llm_next_step=lambda cid: "TOOL_CALL" if cid == 1 else "STOP",
        llm_tool_intent=lambda cid: ("get_deployments", {"service": "payment-service"}),
        stop_text="used listed tools only",
    )
    inst = RuntimeInstance.from_parts(
        engine=engine,
        config=KernelConfig(egress_governance_plugin=NoUndeclaredToolPlugin()),
    )
    obs = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="telemetry"),
    )
    inst.driver.observability.subscribe(obs)
    inst.capture_session_baseline()
    inst.run_user_text("check latency")
    obs.flush()
    obs.close()

    records = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    gov = [r for r in records if r.get("kind") == "governance_decision"]
    blocked = [
        r for r in gov if r.get("decision") == "BLOCK" and "gov_no_undeclared_tool" in (r.get("policy_name") or "")
    ]
    assert blocked, f"no BLOCK from gov_no_undeclared_tool in {gov!r}"
    assert any("get_deployments" in (r.get("reason") or "") for r in blocked)
    assert any(r.get("checkpoint") == "after" for r in blocked)
    assert any(r.get("hook") == "egress" for r in blocked)
