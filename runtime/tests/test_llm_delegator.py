#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LlmDelegator — delegate_to_* execution over CommBus."""

from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator


def _delegator(run_turn, routes=None, allowed_peers=()):
    return LlmDelegator(
        comm=LocalAgentComm(run_turn),
        routes=routes,
        allowed_peers=allowed_peers,
    )


def test_llm_delegator_dispatches_via_run_turn():
    calls: list[tuple[str, str, int, str, str]] = []

    def run_turn(
        agent_id: str, prompt: str, correlation_id: int, caller_call_id: str, context_id: str
    ) -> str:
        calls.append((agent_id, prompt, correlation_id, caller_call_id, context_id))
        return f"ok:{agent_id}"

    delegator = _delegator(run_turn, allowed_peers=["telemetry"])
    out = delegator.call_delegate_tool(
        "delegate_to_telemetry", {"task": "check latency"}, correlation_id=7
    )
    assert out == "ok:telemetry"
    assert calls == [("telemetry", "check latency", 7, "", "")]


def test_llm_delegator_missing_target_on_bus():
    delegator = _delegator(
        lambda aid, task, cid, ccid, ctx_id: (_ for _ in ()).throw(KeyError(aid)),
        allowed_peers=["missing"],
    )
    out = delegator.call_delegate_tool("delegate_to_missing", {"task": "x"})
    assert "not available on bus" in out


def test_llm_delegator_is_delegate_tool():
    delegator = _delegator(lambda aid, task, cid, ccid, ctx_id: "ok")
    assert delegator.is_delegate_tool("delegate_to_x")
    assert not delegator.is_delegate_tool("delegate_to_")
    assert not delegator.is_delegate_tool("send")


def test_llm_delegator_repeats_identical_task_per_session():
    calls: list[str] = []

    def run_turn(
        agent_id: str, prompt: str, correlation_id: int, caller_call_id: str, context_id: str
    ) -> str:
        calls.append(agent_id)
        return f"findings:{agent_id}:{prompt}"

    delegator = _delegator(run_turn, allowed_peers=["telemetry"])
    assert delegator.delegate("telemetry", "task1") == "findings:telemetry:task1"
    repeated = delegator.delegate("telemetry", "task1")
    assert repeated == "findings:telemetry:task1"
    assert calls == ["telemetry", "telemetry"]


def test_llm_delegator_different_tasks_call_peer_again():
    calls: list[tuple[str, str]] = []

    def run_turn(
        agent_id: str, prompt: str, correlation_id: int, caller_call_id: str, context_id: str
    ) -> str:
        calls.append((agent_id, prompt))
        return f"findings:{agent_id}:{prompt}"

    delegator = _delegator(run_turn, allowed_peers=["telemetry"])
    assert delegator.delegate("telemetry", "task1") == "findings:telemetry:task1"
    assert delegator.delegate("telemetry", "task2") == "findings:telemetry:task2"
    assert calls == [("telemetry", "task1"), ("telemetry", "task2")]


def test_llm_delegator_passes_correlation_id_through_to_run_turn():
    """Regression: the delegate_to_* TOOL_CALL's own correlation_id must reach
    run_turn unchanged — it's how the caller resolves this specific sibling's
    own call_id when several delegate_to_* calls fire in one batch (see
    make_workflow_send / ObservabilityOperator.call_id_for)."""
    seen: list[int] = []

    def run_turn(
        agent_id: str, prompt: str, correlation_id: int, caller_call_id: str, context_id: str
    ) -> str:
        seen.append(correlation_id)
        return "ok"

    delegator = _delegator(run_turn, allowed_peers=["a", "b"])
    delegator.call_delegate_tool("delegate_to_a", {"task": "t1"}, correlation_id=3)
    delegator.call_delegate_tool("delegate_to_b", {"task": "t2"}, correlation_id=9)
    assert seen == [3, 9]


def test_llm_delegator_passes_caller_call_id_through_to_run_turn():
    """caller_call_id (this TOOL_CALL's own resolved call_id, attached by the
    driver — see InvokeEngineIo.call_id) must reach run_turn unchanged, so
    the delegate's own execution_start.parent_call_id is a real, native
    value instead of something reconstructed downstream from timestamps."""
    seen: list[str] = []

    def run_turn(
        agent_id: str, prompt: str, correlation_id: int, caller_call_id: str, context_id: str
    ) -> str:
        seen.append(caller_call_id)
        return "ok"

    delegator = _delegator(run_turn, allowed_peers=["a"])
    delegator.call_delegate_tool(
        "delegate_to_a", {"task": "t1"}, correlation_id=3, caller_call_id="tool-call-abc"
    )
    assert seen == ["tool-call-abc"]


def test_llm_delegator_passes_context_id_through_to_run_turn():
    """An explicit context_id tool argument reaches run_turn unchanged, and
    picking a different context_id for the same task bypasses the
    already-consulted-this-session dedupe cache."""
    seen: list[str] = []

    def run_turn(
        agent_id: str, prompt: str, correlation_id: int, caller_call_id: str, context_id: str
    ) -> str:
        seen.append(context_id)
        return f"ok:{context_id}"

    delegator = _delegator(run_turn, allowed_peers=["a"])
    out_a = delegator.call_delegate_tool(
        "delegate_to_a", {"task": "t1", "context_id": "ctx-a"}
    )
    out_b = delegator.call_delegate_tool(
        "delegate_to_a", {"task": "t1", "context_id": "ctx-b"}
    )
    assert seen == ["ctx-a", "ctx-b"]
    assert out_a == "ok:ctx-a"
    assert out_b == "ok:ctx-b"  # not the "already consulted" cache hit
