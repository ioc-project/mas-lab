#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine tool dispatch — delegation contract routing."""

import pytest
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
from mas.runtime.engine.tool_dispatch import execute_engine_tool


def test_execute_engine_tool_routes_delegate_tools():
    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: f"delegated:{aid}:{task}"),
        allowed_peers=["db"],
    )
    out = execute_engine_tool(
        "delegate_to_db",
        delegation=delegator,
        arguments={"task": "check connections"},
    )
    assert out == "delegated:db:check connections"


def test_execute_engine_tool_forwards_caller_call_id_to_delegation():
    """caller_call_id (this TOOL_CALL's own resolved call_id, attached by
    the driver) must reach the DelegationContract unchanged, so a
    delegate's own execution_start.parent_call_id is a real native value."""
    seen: list[str] = []
    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: seen.append(ccid) or f"delegated:{aid}"),
        allowed_peers=["db"],
    )
    execute_engine_tool(
        "delegate_to_db",
        delegation=delegator,
        arguments={"task": "check connections"},
        caller_call_id="tool-call-xyz",
    )
    assert seen == ["tool-call-xyz"]


def test_execute_engine_tool_leaves_user_tool_named_send_to_provider():
    seen: list[str] = []

    class Provider:
        def call_tool(self, tool, arguments, **kwargs):
            seen.append(tool)
            return "from-user-tool"

    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: f"delegated:{aid}"),
        allowed_peers=["db"],
    )
    out = execute_engine_tool(
        "send",
        delegation=delegator,
        arguments={"target": "db", "message": "hi"},
        tool_provider=Provider(),
    )
    assert out == "from-user-tool"
    assert seen == ["send"]
    assert not delegator.is_delegate_tool("send")


def test_execute_engine_tool_routes_ordered_contract_before_manifest_provider():
    calls = []

    class EngineTool:
        def claims(self, tool_name):
            return tool_name == "spawn_subagent"

        def call(self, tool_name, arguments, **kwargs):
            calls.append((tool_name, arguments, kwargs["caller_call_id"]))
            return "spawned"

    result = execute_engine_tool(
        "spawn_subagent",
        engine_contracts=[EngineTool()],
        arguments={"template": "worker", "task": "review"},
        caller_call_id="parent-call",
    )

    assert result == "spawned"
    assert calls == [
        ("spawn_subagent", {"template": "worker", "task": "review"}, "parent-call")
    ]


def test_execute_engine_tool_uses_manifest_provider(tmp_path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calc.py").write_text(
        """
class CalcTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name != "calculator":
            return None
        return {"result": 65536}
""",
        encoding="utf-8",
    )
    import yaml

    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {
                    "description": "calc",
                    "impl": {"module_path": "./calc.py", "class_name": "CalcTool"},
                },
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        tmp_path,
    )
    out = execute_engine_tool(
        "calculator",
        arguments={"expression": "2**16"},
        tool_provider=provider,
    )
    assert "65536" in out


def test_unknown_tool_raises(tmp_path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calc.py").write_text(
        """
class CalcTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name != "calculator":
            return None
        return {"ok": True}
""",
        encoding="utf-8",
    )
    import yaml

    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {"impl": {"module_path": "./calc.py", "class_name": "CalcTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        tmp_path,
    )
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    with pytest.raises(ClassifiedFailure, match="not found") as caught:
        execute_engine_tool("missing", tool_provider=provider)
    assert caught.value.failure_class is FailureClass.APPLICATION
    assert caught.value.code == "TOOL_UNKNOWN"
