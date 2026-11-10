#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.runtime.engine.manifest_tool_provider import ManifestToolLoadError, build_manifest_tool_provider


def test_application_tool_named_like_a_system_plugin_tool_is_refused(tmp_path: Path) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "clone.py").write_text(
        """
class CloneTool:
    def on_collect_tools(self, **_):
        return [{"name": "inform_user", "description": "app", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return "app"
""",
        encoding="utf-8",
    )
    (tool_dir / "clone.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "apiVersion": "mas/v1",
                "metadata": {"name": "clone"},
                "spec": {
                    "description": "clone",
                    "parameters": [],
                    "impl": {"module_path": "./clone.py", "class_name": "CloneTool"},
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ManifestToolLoadError, match="collide"):
        build_manifest_tool_provider(
            [{"ref": "tools/clone.tool.yaml"}],
            tmp_path,
            spec={"system_plugins": ["inform_user"]},
        )


def _write_named_tool(tmp_path: Path, *, module: str, class_name: str, tool_name: str, result: str) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir(exist_ok=True)
    (tool_dir / f"{module}.py").write_text(
        f"""
class {class_name}:
    def on_collect_tools(self, **_):
        return [{{"name": {tool_name!r}, "description": "app", "parameters": {{"type": "object", "properties": {{}}}}}}]
    def on_execute_tool(self, name, args, **_):
        return {result!r}
""",
        encoding="utf-8",
    )
    (tool_dir / f"{module}.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "apiVersion": "mas/v1",
                "metadata": {"name": module},
                "spec": {
                    "description": "app",
                    "parameters": [],
                    "impl": {"module_path": f"./{module}.py", "class_name": class_name},
                },
            }
        ),
        encoding="utf-8",
    )


def test_system_plugin_tools_are_listed_before_application_tools(tmp_path: Path) -> None:
    _write_named_tool(tmp_path, module="calc_clone", class_name="CalcClone", tool_name="calc_clone", result="ok")
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calc_clone.tool.yaml"}],
        tmp_path,
        spec={"system_plugins": ["inform_user"]},
    )
    names = [str(spec.get("name") or "") for spec in provider.list_tools()]
    assert names.index("inform_user") < names.index("calc_clone")


def test_user_tool_named_send_runs_when_send_plugin_is_loaded(tmp_path: Path) -> None:
    """``send`` is not an advertised tool name; a user tool with that name is not hijacked."""
    from types import SimpleNamespace

    from mas.runtime.engine.tool_dispatch import execute_engine_tool
    from mas.runtime.system_plugins.dispatch import SystemPluginDispatcher

    _write_named_tool(tmp_path, module="send_clone", class_name="SendClone", tool_name="send", result="user-send")
    provider = build_manifest_tool_provider(
        [{"ref": "tools/send_clone.tool.yaml"}],
        tmp_path,
        spec={"system_plugins": ["send"]},
    )
    names = {str(spec.get("name") or "") for spec in provider.list_tools()}
    assert "send" in names
    local = provider._local_provider()
    bound = local.system_plugins
    engine = SimpleNamespace(engine_tool_contracts=(SystemPluginDispatcher(bound),))
    result = execute_engine_tool(
        "send",
        arguments={},
        engine_contracts=engine.engine_tool_contracts,
        tool_provider=provider,
    )
    assert result == "user-send"


def test_user_tool_named_delegate_to_peer_runs_when_send_does_not_advertise_it(tmp_path: Path) -> None:
    """``send`` claims only advertised ``delegate_to_<id>`` names, not every such spelling."""
    from types import SimpleNamespace

    from mas.runtime.engine.tool_dispatch import execute_engine_tool
    from mas.runtime.system_plugins.dispatch import SystemPluginDispatcher

    _write_named_tool(
        tmp_path,
        module="delegate_clone",
        class_name="DelegateClone",
        tool_name="delegate_to_worker",
        result="user-delegate",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/delegate_clone.tool.yaml"}],
        tmp_path,
        spec={"system_plugins": ["send"]},
    )
    names = {str(spec.get("name") or "") for spec in provider.list_tools()}
    assert "delegate_to_worker" in names
    bound = provider._local_provider().system_plugins
    assert not bound.claims("delegate_to_worker")
    engine = SimpleNamespace(engine_tool_contracts=(SystemPluginDispatcher(bound),))
    result = execute_engine_tool(
        "delegate_to_worker",
        arguments={"task": "go"},
        engine_contracts=engine.engine_tool_contracts,
        tool_provider=provider,
    )
    assert result == "user-delegate"
