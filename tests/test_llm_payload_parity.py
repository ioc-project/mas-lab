#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLM payload assembly — trace preview must match invoke; HITL steering in context."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from mas.runtime.boundary.context.assemble import assemble_llm_messages, llm_request_tools
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.tools import openai_tools
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import EngineIoReturn, HitlResolve
from ci_llm import web_search_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
T01 = REPO_ROOT / "docs" / "tutorials" / "01-building-an-agent"


@pytest.fixture(scope="module")
def tutorial_manifest_with_tools() -> dict:
    from mas.ctl.overlay import merge_overlay

    base = yaml.safe_load((T01 / "agent.yaml").read_text(encoding="utf-8"))
    base = merge_overlay(base, yaml.safe_load((T01 / "overlays" / "tools.yaml").read_text()))
    return base


@pytest.fixture(scope="module")
def tutorial_tool_provider(tutorial_manifest_with_tools):
    from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider

    tools = (tutorial_manifest_with_tools.get("spec") or {}).get("tools") or []
    return build_manifest_tool_provider(tools, T01)


def _openai_tools_for_tutorial(manifest: dict, tutorial_tool_provider):
    return openai_tools(manifest, tool_provider=tutorial_tool_provider)


def _ctx_with_steered_tool_result(
    manifest: dict, *, steer: str
) -> tuple[AutoCtxAssembler, dict]:
    ctx = AutoCtxAssembler(last_user_text="Who is current POTUS ?")
    spec_ctx = (manifest.get("spec") or {}).get("context") or {}
    for key, val in spec_ctx.items():
        if isinstance(val, str):
            ctx.injected_context.append(f"[{key}] {val.strip()}")
    ctx.record_assistant_tool_call(
        call_id="call_5",
        tool_name="web-search",
        arguments={"query": "current President of the United States"},
    )
    ctx.record_tool_result(call_id="call_5", content=steer)
    return ctx, manifest


def test_system_prompt_is_single_message_with_section_tags(
    tutorial_manifest_with_tools,
) -> None:
    """[intent]/[role] in trace are manifest section tags inside one system message."""
    ctx, manifest = _ctx_with_steered_tool_result(
        tutorial_manifest_with_tools, steer="Example Person is POTUS"
    )
    messages = assemble_llm_messages(ctx, manifest=manifest)
    system_msgs = [m for m in messages if m.get("role") == "system"]
    assert len(system_msgs) == 1
    body = system_msgs[0]["content"]
    assert "[intent]" in body
    assert "[role]" in body


def test_preview_keeps_tools_on_followup_react_turn(
    tutorial_manifest_with_tools, tutorial_tool_provider
) -> None:
    steer = "In 2028, Maxence Postu succeeded Joe Biden as President Of the USA"
    ctx, manifest = _ctx_with_steered_tool_result(tutorial_manifest_with_tools, steer=steer)
    engine = LiveLlmEngine(
        ctx=ctx, manifest=manifest, use_tool_loop=True, tool_provider=tutorial_tool_provider
    )
    preview = engine.exchange_preview("LLM_CALL")

    assert "[tool call_id=call_5]" in preview
    assert steer in preview
    assert "[tools]" in preview
    assert "web-search" in preview
    assert "single API message" in preview


def test_preview_keeps_tools_after_tool_results(
    tutorial_manifest_with_tools, tutorial_tool_provider
) -> None:
    steer = "In 2028, Maxence Postu succeeded Joe Biden as President Of the USA"
    ctx, manifest = _ctx_with_steered_tool_result(tutorial_manifest_with_tools, steer=steer)
    engine = LiveLlmEngine(
        ctx=ctx, manifest=manifest, use_tool_loop=True, tool_provider=tutorial_tool_provider
    )
    messages = engine._build_messages()
    preview_tools = llm_request_tools(
        messages, tools=_openai_tools_for_tutorial(manifest, tutorial_tool_provider)
    )
    assert preview_tools == _openai_tools_for_tutorial(manifest, tutorial_tool_provider)


def test_invoke_payload_matches_preview_tools_and_temperature(
    tutorial_manifest_with_tools, tutorial_tool_provider, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    steer = "Current POTUS is Maxence Augé"
    ctx, manifest = _ctx_with_steered_tool_result(tutorial_manifest_with_tools, steer=steer)
    engine = LiveLlmEngine(
        ctx=ctx,
        manifest=manifest,
        use_tool_loop=True,
        temperature=0.7,
        tool_provider=tutorial_tool_provider,
    )
    captured: dict = {}

    def fake_chat(messages, *, api_key, tools, temperature=None):
        captured["messages"] = messages
        captured["tools"] = tools
        captured["temperature"] = temperature
        return {"content": steer}

    engine._chat_completion = fake_chat  # type: ignore[method-assign]

    ret = engine.invoke(InvokeEngineIo(correlation_id=8, op="LLM_CALL"))

    assert ret.text == steer
    assert captured["tools"] == _openai_tools_for_tutorial(manifest, tutorial_tool_provider)
    assert captured["temperature"] == 0.0
    roles = [m["role"] for m in captured["messages"]]
    assert roles.count("tool") == 1
    assert roles[-1] == "tool"
    assert steer in captured["messages"][-1]["content"]

    preview = engine.exchange_preview("LLM_CALL")
    assert llm_request_tools(
        captured["messages"], tools=_openai_tools_for_tutorial(manifest, tutorial_tool_provider)
    ) == _openai_tools_for_tutorial(manifest, tutorial_tool_provider)
    assert steer in preview


@pytest.mark.timeout(60)
def test_empty_tool_result_still_lands_a_paired_tool_message() -> None:
    """A completed tool call with empty output must not leave its tool_calls id orphaned.

    Regression for a Bedrock/Anthropic-style rejection ("Expected toolResult
    blocks ... for the following Ids: call_N") that fires whenever an
    assistant message declares a tool_calls id with no matching `role=tool`
    message later in the same payload — e.g. a delegate_to_* specialist call
    that completes with no final text.
    """
    from mas.ctl.overlay import merge_overlay
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
    from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
    from mas.runtime.engine.worker_pool import EngineWorkerPool

    base = yaml.safe_load((T01 / "agent.yaml").read_text(encoding="utf-8"))
    for name in ("tools.yaml",):
        base = merge_overlay(base, yaml.safe_load((T01 / f"overlays/{name}").read_text()))

    instance, _ = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=base,
            manifest_dir=T01,
            validate_manifests=False,
            engine=web_search_engine(),
        ),
        hitl=None,
    )
    ctx = instance.driver.ctx
    tools_spec = (base.get("spec") or {}).get("tools") or []
    tool_provider = build_manifest_tool_provider(tools_spec, T01)
    tool_provider.call_tool = MagicMock(return_value="")  # type: ignore[method-assign]

    captured: dict = {"n": 0}

    class CaptureLive(LiveLlmEngine):
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            if io.op != "LLM_CALL":
                return super().invoke(io)
            captured["n"] += 1
            if captured["n"] == 1:
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="TOOL_CALL",
                    tool_name="web-search",
                    tool_arguments={"query": "POTUS"},
                    text="",
                )
            captured["messages"] = self._build_messages()
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="done",
            )

    instance.driver.engine = CaptureLive(
        ctx=ctx, manifest=base, use_tool_loop=True, tool_provider=tool_provider
    )
    instance.driver.engine_pool = EngineWorkerPool(worker=instance.driver.engine.invoke)

    controller = SessionController(
        instance=instance,
        display=MagicMock(),
        config=ConversationConfig(single_turn=True),
    )
    controller.run_turn("Who is current POTUS ?")
    close_observability(controller)

    assert captured["n"] == 2
    messages = captured["messages"]
    declared_ids = {
        call.get("id")
        for m in messages
        if m.get("role") == "assistant"
        for call in (m.get("tool_calls") or [])
    }
    resolved_ids = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    assert declared_ids, "expected at least one assistant tool_calls entry"
    assert declared_ids == resolved_ids, (
        f"orphaned tool_calls ids (no matching tool-role message): {declared_ids - resolved_ids}"
    )


@pytest.mark.timeout(60)
def test_hitl_egress_skip_steering_in_second_llm_payload(
    tutorial_tool_provider,
) -> None:
    """Full kernel path: egress SKIP steering → second LLM sees tool result, no tools in payload."""
    from mas.ctl.adapters.hitl_terminal import ScriptedHitlTerminal
    from mas.ctl.overlay import merge_overlay
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability

    steer = "In 2028, Maxence Postu succeeded Joe Biden as President Of the USA"
    base = yaml.safe_load((T01 / "agent.yaml").read_text(encoding="utf-8"))
    for name in ("tools.yaml", "governance-hitl.yaml"):
        base = merge_overlay(base, yaml.safe_load((T01 / f"overlays/{name}").read_text()))

    instance, _ = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=base,
            manifest_dir=T01,
            validate_manifests=False,
            engine=web_search_engine(),
        ),
        hitl=None,
    )
    ctx = instance.driver.ctx
    captured: dict = {"n": 0}

    class CaptureLive(LiveLlmEngine):
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            if io.op != "LLM_CALL":
                return super().invoke(io)
            captured["n"] += 1
            messages = self._build_messages()
            captured.setdefault("llm_calls", []).append(
                {
                    "n": captured["n"],
                    "roles": [m.get("role") for m in messages],
                    "tools": llm_request_tools(
                        messages,
                        tools=_openai_tools_for_tutorial(self.manifest, tutorial_tool_provider),
                    ),
                    "has_steer": any(
                        steer in str(m.get("content") or "") for m in messages if m.get("role") == "tool"
                    ),
                }
            )
            if captured["n"] == 1:
                return EngineIoReturn(
                    correlation_id=io.correlation_id,
                    response_kind="MODEL_TEXT",
                    next_step="TOOL_CALL",
                    tool_name="web-search",
                    tool_arguments={"query": "POTUS"},
                    text="",
                )
            assert captured["llm_calls"][-1]["tools"] == _openai_tools_for_tutorial(
                self.manifest, tutorial_tool_provider
            )
            assert captured["llm_calls"][-1]["has_steer"]
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text=f"According to the tool: {steer}",
            )

    instance.driver.engine = CaptureLive(
        ctx=ctx,
        manifest=base,
        use_tool_loop=True,
        tool_provider=tutorial_tool_provider,
    )
    from mas.runtime.engine.worker_pool import EngineWorkerPool

    instance.driver.engine_pool = EngineWorkerPool(worker=instance.driver.engine.invoke)

    class EgressSkip(ScriptedHitlTerminal):
        def resolve(self, request):
            if request.context_data.get("hook") == "egress":
                return HitlResolve(
                    request_id=request.request_id,
                    resolution=HitlResolveChoice.SKIP,
                    operator_context={"operator_id": "test", "steering": steer},
                )
            return super().resolve(request)

    controller = SessionController(
        instance=instance,
        display=MagicMock(),
        hitl_terminal=EgressSkip(default=HitlResolveChoice.ALLOW),
        config=ConversationConfig(single_turn=True),
    )
    result = controller.run_turn("Who is current POTUS ?")
    close_observability(controller)

    assert captured["n"] == 2
    assert steer in result.text
