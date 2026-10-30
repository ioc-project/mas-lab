#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TOOL_CALL runs for: tool middleware, including destination wraps."""

from __future__ import annotations

from types import SimpleNamespace

from mas.runtime.engine.infra_pipeline import (
    apply_middleware,
    filter_pipeline_for_kind,
    is_cache_middleware,
    stamp_middleware_step,
)
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


class _BareProvider:
    def chat_completion(self, **kwargs: object) -> dict[str, str]:
        del kwargs
        return {"role": "assistant", "content": "ok"}

    async def achat_completion(self, **kwargs: object) -> dict[str, str]:
        return self.chat_completion(**kwargs)


def _engine() -> LiveLlmEngine:
    return LiveLlmEngine(manifest=None, use_cache=False, llm_provider=_BareProvider())


def _tool_io() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="TOOL_CALL")


def test_undeclared_step_wraps_llm_only() -> None:
    stamped = stamp_middleware_step({"middleware": "my_custom_audit"})
    assert stamped["for"] == ["llm"]
    assert stamped["per"] == "destination"
    assert filter_pipeline_for_kind([stamped], "tool") == []
    assert [step["middleware"] for step in filter_pipeline_for_kind([stamped], "llm")] == [
        "my_custom_audit"
    ]


def test_empty_for_list_is_llm_not_every_kind() -> None:
    stamped = stamp_middleware_step({"middleware": "my_custom_audit", "for": []})
    assert stamped["for"] == ["llm"]
    assert filter_pipeline_for_kind([stamped], "tool") == []


def test_install_tool_pipeline_runs_on_tool_call() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [
            {
                "middleware": "fault_inject",
                "for": ["tool"],
                "per": "all",
                "params": {"rate": 1.0, "message": "injected fault"},
            }
        ]
    )
    ret = engine.invoke(_tool_io())
    assert engine._tool_stack is not None
    assert ret.response_kind == "ERROR"
    assert "injected fault" in (ret.text or "")


def _dest_step(message: str) -> dict[str, object]:
    return {
        "middleware": "fault_inject",
        "for": ["tool"],
        "per": "destination",
        "params": {"rate": 1.0, "message": message},
    }


def test_destination_tool_wrap_runs_on_claimed_tool() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "local": {
                "protocol": "local",
                "tools": ["alpha"],
                "pipeline": [_dest_step("dest fault")],
            }
        },
    )
    engine.set_scheduled_tool("alpha", {})
    ret = engine.invoke(_tool_io())
    assert engine._tool_stack is not None
    assert engine._tool_destinations
    assert ret.response_kind == "ERROR"
    assert "dest fault" in (ret.text or "")


def test_one_dest_server_does_not_wrap_an_unclaimed_tool() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "local": {
                "protocol": "local",
                "tools": ["alpha"],
                "pipeline": [_dest_step("dest fault")],
            }
        },
    )
    engine.set_scheduled_tool("beta", {})
    ret = engine.invoke(_tool_io())
    assert "dest fault" not in (ret.text or "")


def test_star_dest_does_not_wrap_a_tool_another_server_lists() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": "*",
                "pipeline": [_dest_step("A-FAULT")],
            },
            "b": {"protocol": "mcp", "tools": ["b_tool"]},
        },
    )
    engine.set_scheduled_tool("b_tool", {})
    claimed = engine.invoke(_tool_io())
    assert "A-FAULT" not in (claimed.text or "")
    engine.set_scheduled_tool("other", {})
    leftover = engine.invoke(_tool_io())
    assert leftover.response_kind == "ERROR"
    assert "A-FAULT" in (leftover.text or "")


def test_unlisted_dest_does_not_wrap_a_tool_another_server_lists() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "pipeline": [_dest_step("A-FAULT")],
            },
            "b": {"protocol": "mcp", "tools": ["b_tool"]},
        },
    )
    engine.set_scheduled_tool("b_tool", {})
    claimed = engine.invoke(_tool_io())
    assert "A-FAULT" not in (claimed.text or "")
    engine.set_scheduled_tool("other", {})
    leftover = engine.invoke(_tool_io())
    assert leftover.response_kind == "ERROR"
    assert "A-FAULT" in (leftover.text or "")


def test_empty_string_tools_dest_is_star_not_empty_explicit() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": "",
                "pipeline": [_dest_step("A-FAULT")],
            },
            "b": {"protocol": "mcp", "tools": ["b_tool"]},
        },
    )
    engine.set_scheduled_tool("b_tool", {})
    claimed = engine.invoke(_tool_io())
    assert "A-FAULT" not in (claimed.text or "")
    engine.set_scheduled_tool("other", {})
    leftover = engine.invoke(_tool_io())
    assert leftover.response_kind == "ERROR"
    assert "A-FAULT" in (leftover.text or "")


def test_star_dest_does_not_wrap_system_tools() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": "*",
                "pipeline": [_dest_step("A-FAULT")],
            }
        },
    )
    for name in ("request_human_input", "inform_user", "spawn_subagent", "create_subagent"):
        engine.set_scheduled_tool(name, {})
        ret = engine.invoke(_tool_io())
        assert "A-FAULT" not in (ret.text or ""), name
    engine.set_scheduled_tool("other", {})
    leftover = engine.invoke(_tool_io())
    assert leftover.response_kind == "ERROR"
    assert "A-FAULT" in (leftover.text or "")


def test_star_dest_with_advertised_names_does_not_wrap_unknown() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": "*",
                "advertised": ["a_tool"],
                "pipeline": [_dest_step("A-FAULT")],
            }
        },
    )
    engine.set_scheduled_tool("a_tool", {})
    hit = engine.invoke(_tool_io())
    assert hit.response_kind == "ERROR"
    assert "A-FAULT" in (hit.text or "")
    engine.set_scheduled_tool("other", {})
    miss = engine.invoke(_tool_io())
    assert "A-FAULT" not in (miss.text or "")


def test_star_dest_uses_provider_advertised_names() -> None:
    engine = _engine()
    engine.tool_provider = SimpleNamespace(
        _routes={"a_tool": SimpleNamespace(provider_name="a")},
    )
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": "*",
                "pipeline": [_dest_step("A-FAULT")],
            }
        },
    )
    engine.set_scheduled_tool("a_tool", {})
    hit = engine.invoke(_tool_io())
    assert "A-FAULT" in (hit.text or "")
    engine.set_scheduled_tool("other", {})
    miss = engine.invoke(_tool_io())
    assert "A-FAULT" not in (miss.text or "")


def test_apply_middleware_passes_call_kinds_into_wrap() -> None:
    class _Inner:
        def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
            return EngineIoReturn(
                correlation_id=io.correlation_id,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="raw",
            )

        def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
            del op, correlation_id
            return ""

    wrapped = apply_middleware(
        _Inner(),
        {
            "middleware": "fault_inject",
            "for": ["tool"],
            "params": {"rate": 1.0, "message": "tool fault"},
        },
    )
    assert wrapped.call_kinds == ("tool",)
    hit = wrapped.invoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL"))
    assert hit.response_kind == "ERROR"
    assert "tool fault" in (hit.text or "")
    miss = wrapped.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert "tool fault" not in (miss.text or "")


def test_explicit_dest_still_wraps_a_listed_system_tool() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": ["request_human_input"],
                "pipeline": [_dest_step("A-FAULT")],
            }
        },
    )
    engine.set_scheduled_tool("request_human_input", {})
    ret = engine.invoke(_tool_io())
    assert ret.response_kind == "ERROR"
    assert "A-FAULT" in (ret.text or "")


def test_two_servers_claiming_one_tool_use_raw_dispatch() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "a": {
                "protocol": "mcp",
                "tools": ["shared"],
                "pipeline": [_dest_step("A-FAULT")],
            },
            "b": {
                "protocol": "mcp",
                "tools": ["shared"],
                "pipeline": [_dest_step("B-FAULT")],
            },
        },
    )
    engine.set_scheduled_tool("shared", {})
    ret = engine.invoke(_tool_io())
    assert "A-FAULT" not in (ret.text or "")
    assert "B-FAULT" not in (ret.text or "")


def test_dest_wrap_reads_pending_tool_by_correlation_id() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "local": {
                "protocol": "local",
                "tools": ["alpha"],
                "pipeline": [_dest_step("dest fault")],
            }
        },
    )
    engine.set_scheduled_tool("beta", {})
    engine.set_tool_for_correlation(9, "alpha", {})
    ret = engine.invoke(InvokeEngineIo(correlation_id=9, op="TOOL_CALL"))
    assert ret.response_kind == "ERROR"
    assert "dest fault" in (ret.text or "")


def test_dest_exchange_preview_reads_pending_tool_by_correlation_id() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "local": {
                "protocol": "local",
                "tools": ["alpha"],
                "pipeline": [_dest_step("dest fault")],
            }
        },
    )
    engine.set_scheduled_tool("beta", {})
    engine.set_tool_for_correlation(9, "alpha", {})
    stack = engine._tool_stack
    assert stack is not None
    dest = stack.exchange_preview("TOOL_CALL", correlation_id=9)
    raw = stack.exchange_preview("TOOL_CALL")
    miss = stack.exchange_preview("TOOL_CALL", correlation_id=2)
    assert "fault_inject" in dest
    assert "fault_inject" not in raw
    assert "fault_inject" not in miss


def test_tool_name_equal_to_server_id_is_not_a_claim() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "search": {
                "protocol": "mcp",
                "tools": ["alpha"],
                "pipeline": [_dest_step("A-FAULT")],
            }
        },
    )
    engine.set_scheduled_tool("search", {})
    ret = engine.invoke(_tool_io())
    assert "A-FAULT" not in (ret.text or "")


def test_several_dest_servers_wrap_the_server_that_claims_the_tool() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [],
        destinations={
            "mcp": {
                "protocol": "mcp",
                "tools": ["search"],
                "pipeline": [_dest_step("mcp fault")],
            },
            "local": {
                "protocol": "local",
                "tools": ["fs"],
                "pipeline": [_dest_step("local fault")],
            },
        },
    )
    engine.set_scheduled_tool("search", {})
    mcp = engine.invoke(_tool_io())
    assert mcp.response_kind == "ERROR"
    assert "mcp fault" in (mcp.text or "")
    assert "local fault" not in (mcp.text or "")

    engine.set_scheduled_tool("fs", {})
    local = engine.invoke(_tool_io())
    assert local.response_kind == "ERROR"
    assert "local fault" in (local.text or "")
    assert "mcp fault" not in (local.text or "")

    engine.set_scheduled_tool("other", {})
    other = engine.invoke(_tool_io())
    assert "mcp fault" not in (other.text or "")
    assert "local fault" not in (other.text or "")


def test_llm_call_does_not_run_tool_stack() -> None:
    engine = _engine()
    engine.install_tool_pipeline(
        [
            {
                "middleware": "fault_inject",
                "for": ["tool"],
                "per": "all",
                "params": {"rate": 1.0, "message": "injected fault"},
            }
        ]
    )
    ret = engine.invoke(InvokeEngineIo(correlation_id=1, op="LLM_CALL"))
    assert ret.response_kind != "ERROR"
    assert "injected fault" not in (ret.text or "")


def test_is_cache_middleware_false_when_registry_empty(monkeypatch) -> None:
    class _Empty:
        def get_entry(self, urn: str) -> None:
            del urn
            return None

        def resolve_by_type(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            return None

    monkeypatch.setattr("mas.runtime.registry.get_registry", lambda: _Empty())
    assert is_cache_middleware({"middleware": "llm_cache"}) is False
