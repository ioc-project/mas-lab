#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for request_human_input's configurable timeout and auto-resolve decision."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from mas.library.standard.plugins.hitl.contracts import AutoResolveHitlContract
from mas.library.standard.plugins.system_tools.request_human_input import RequestHumanInputTool
from mas.runtime.boundary.hitl.registry import get_hitl_resolver_registry
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider


@dataclass
class _FakeCtx:
    session_id: str
    agent_id: str = "finance-agent"
    correlation_id: int = 1


HITL = ("request_human_input",)


def _plugin_state(provider, name: str = "request_human_input"):
    local = provider._local_provider()
    for row in local.system_plugins:
        if row.name == name or row.urn.endswith(f".{name}"):
            return row.state
    raise AssertionError(f"system plugin {name!r} was not loaded")


@pytest.fixture()
def empty_tool_tree(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# timeout: default None (block until resolved, no TimeoutError), configurable
# per call and via the manifest's spec.tools system-tool entry
# ---------------------------------------------------------------------------


def test_input_schema_defaults_timeout_to_none():
    """No timeout argument -> RequestHitlSignal.timeout is None (block forever)."""
    from mas.runtime.system_tools.signal import RequestHitlSignal

    tool = RequestHumanInputTool()
    with pytest.raises(RequestHitlSignal) as exc_info:
        tool.execute(question="Approve?", question_type="CONFIRM", choices=["approve", "reject"])
    assert exc_info.value.timeout is None


def test_default_wrapper_has_no_timeout_when_nothing_configured(empty_tool_tree: Path):
    provider = build_manifest_tool_provider([], empty_tool_tree, system_plugins=HITL)
    wrapper = _plugin_state(provider)
    assert wrapper._default_timeout_seconds is None


def test_manifest_params_set_the_default_timeout(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"timeout": 5}}]},
    )
    wrapper = _plugin_state(provider)
    assert wrapper._default_timeout_seconds == 5


def test_no_timeout_configured_blocks_until_resolved(empty_tool_tree: Path):
    """With neither a manifest default nor a call-time timeout, the wrapper
    must wait indefinitely for resolution -- not silently apply some other
    default. Resolved promptly from another thread here so the test itself
    doesn't hang."""
    provider = build_manifest_tool_provider([], empty_tool_tree, system_plugins=HITL)
    ctx = _FakeCtx(session_id="sess-block")
    registry = get_hitl_resolver_registry()
    result: dict = {}

    def resolve_soon():
        # Give on_execute_tool a moment to register before resolving.
        for _ in range(50):
            if registry.has_pending(ctx.session_id, ctx.agent_id):
                break
            time.sleep(0.01)
        registry.resolve(ctx.session_id, ctx.agent_id, ctx.correlation_id, choice="approve", steering="")

    threading.Thread(target=resolve_soon, daemon=True).start()

    result["value"] = provider.call_tool(
        "request_human_input",
        {"question": "Approve?", "question_type": "CONFIRM", "choices": ["approve", "reject"]},
        ctx=ctx,
    )
    assert result["value"]["choice"] == "approve"


def test_call_time_timeout_raises_after_configured_seconds(empty_tool_tree: Path):
    provider = build_manifest_tool_provider([], empty_tool_tree, system_plugins=HITL)
    ctx = _FakeCtx(session_id="sess-timeout")

    with pytest.raises(TimeoutError):
        provider.call_tool(
            "request_human_input",
            {
                "question": "Approve?",
                "question_type": "CONFIRM",
                "choices": ["approve", "reject"],
                "timeout": 0.05,
            },
            ctx=ctx,
        )


def test_call_time_timeout_overrides_manifest_default(empty_tool_tree: Path):
    """A longer manifest default must not stop a call from asking for a
    shorter timeout for that one request."""
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"timeout": 60}}]},
    )
    ctx = _FakeCtx(session_id="sess-override")

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        provider.call_tool(
            "request_human_input",
            {
                "question": "Approve?",
                "question_type": "CONFIRM",
                "choices": ["approve", "reject"],
                "timeout": 0.05,
            },
            ctx=ctx,
        )
    assert time.monotonic() - start < 5


# ---------------------------------------------------------------------------
# auto-resolve decision: defaults to "approve", configurable
# ---------------------------------------------------------------------------


def test_auto_resolve_defaults_to_approve(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [], empty_tool_tree, system_plugins=HITL, hitl_contract=AutoResolveHitlContract()
    )
    ctx = _FakeCtx(session_id="sess-auto-default")

    result = provider.call_tool(
        "request_human_input",
        {"question": "Approve?", "question_type": "CONFIRM", "choices": ["approve", "reject"]},
        ctx=ctx,
    )
    assert result == {"choice": "approve", "steering": "", "question": "Approve?", "resolved": True}


def test_auto_resolve_ignores_process_environment(empty_tool_tree: Path, monkeypatch):
    monkeypatch.setenv("MAS_HITL_AUTO_RESOLVE", "1")
    monkeypatch.setenv("MAS_HITL_AUTO_RESOLVE_DECISION", "reject")
    provider = build_manifest_tool_provider([], empty_tool_tree, system_plugins=HITL)
    ctx = _FakeCtx(session_id="sess-auto-env")

    with pytest.raises(TimeoutError):
        provider.call_tool(
            "request_human_input",
            {
                "question": "Approve?",
                "question_type": "CONFIRM",
                "choices": ["approve", "reject"],
                "timeout": 0.05,
            },
            ctx=ctx,
        )


def test_auto_resolve_decision_configurable_via_manifest_params(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"auto_resolve_decision": "reject"}}]},
        hitl_contract=AutoResolveHitlContract(),
    )
    ctx = _FakeCtx(session_id="sess-auto-manifest")

    result = provider.call_tool(
        "request_human_input",
        {"question": "Approve?", "question_type": "CONFIRM", "choices": ["approve", "reject"]},
        ctx=ctx,
    )
    assert result["choice"] == "reject"


def test_default_max_question_length_matches_schema_constant():
    from mas.library.standard.plugins.system_tools.request_human_input import DEFAULT_MAX_QUESTION_LENGTH

    tool = RequestHumanInputTool()
    assert tool.max_question_length == DEFAULT_MAX_QUESTION_LENGTH
    with pytest.raises(ValueError):
        tool.execute(question="x" * (DEFAULT_MAX_QUESTION_LENGTH + 1))


def test_manifest_params_configure_max_question_length(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"max_question_length": 8000}}]},
    )
    wrapper = _plugin_state(provider)
    assert wrapper._tool.max_question_length == 8000
    # A question that would fail the default 2000-char cap must pass under the
    # manifest-configured 8000-char cap (issue #65 follow-up: a more verbose
    # model's output shouldn't fail validation just because the default is small).
    from mas.runtime.system_tools.signal import RequestHitlSignal

    long_question = "x" * 3000
    with pytest.raises(RequestHitlSignal):
        wrapper._tool.execute(question=long_question, choices=["a", "b"])


def test_configured_max_question_length_still_rejects_beyond_it(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"max_question_length": 100}}]},
    )
    wrapper = _plugin_state(provider)
    with pytest.raises(ValueError):
        wrapper._tool.execute(question="x" * 101)


def test_advertised_schema_reflects_configured_max_question_length():
    tool = RequestHumanInputTool(max_question_length=8000)
    schema = tool.get_parameters_schema()
    assert schema["properties"]["question"]["maxLength"] == 8000


def test_manifest_auto_resolve_decision_wins_over_host_default(empty_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        empty_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"auto_resolve_decision": "escalate"}}]},
        hitl_contract=AutoResolveHitlContract("reject"),
    )
    ctx = _FakeCtx(session_id="sess-auto-precedence")

    result = provider.call_tool(
        "request_human_input",
        {"question": "Approve?", "question_type": "CONFIRM", "choices": ["approve", "reject", "escalate"]},
        ctx=ctx,
    )
    assert result["choice"] == "escalate"
