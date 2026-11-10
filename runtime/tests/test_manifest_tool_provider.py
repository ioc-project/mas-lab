#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest tool provider tests."""

from pathlib import Path

import pytest
import yaml
from mas.runtime.engine.manifest_tool_provider import (
    ManifestToolLoadError,
    build_manifest_tool_provider,
)
from mas.runtime.engine.tool_dispatch import execute_engine_tool
from mas.runtime.engine.tools import openai_tools
from mas.runtime.system_plugins import SystemPluginBase, ToolDefinition


class _DynamicSystemPlugin(SystemPluginBase):
    name = "dynamic_system_tool"

    def tools(self, ctx=None):
        return (ToolDefinition(name="dynamic_system_tool", description="dynamic"),)

    def bind(self, session):
        return {}

    def call(self, tool, args, *, state, context):
        return "registered" if tool == "dynamic_system_tool" else None


@pytest.fixture()
def calculator_tool_tree(tmp_path: Path) -> Path:
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
    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "apiVersion": "mas/v1",
                "metadata": {"name": "calculator"},
                "spec": {
                    "description": "calc",
                    "parameters": [
                        {"name": "expression", "type": "string", "required": True},
                    ],
                    "impl": {
                        "module_path": "./calc.py",
                        "class_name": "CalcTool",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_build_manifest_tool_provider_from_ref(calculator_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        calculator_tool_tree,
    )
    names = [t["function"]["name"] for t in provider.list_openai_tools()]
    assert names == ["calculator"]
    out = provider.call_tool("calculator", {"expression": "2**16"})
    assert out["result"] == 65536


def test_spec_tools_load_with_external_only_overlay(calculator_tool_tree: Path):
    class ExternalProvider:
        def list_tools(self, *, ctx=None):
            return []

        def call_tool(self, tool_name, arguments, *, ctx=None, user="", **kwargs):
            raise AssertionError(f"unexpected tool call: {tool_name}")

    external = ExternalProvider()
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        calculator_tool_tree,
        overlay_providers=[external],
    )

    assert provider._registry.local_provider() is not None
    assert external in provider._overlay_providers
    assert [tool["function"]["name"] for tool in provider.list_openai_tools()] == ["calculator"]


def test_system_plugin_entry_enables_the_tool_without_loading_a_module(calculator_tool_tree: Path):
    """A ``spec.system_plugins`` grant must not be loaded as a tools module."""
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        calculator_tool_tree,
        spec={"system_plugins": ["request_human_input"]},
    )
    names = {t["function"]["name"] for t in provider.list_openai_tools()}
    assert names == {"calculator", "request_human_input"}


def test_system_plugin_registry_supports_new_registered_tool_names(calculator_tool_tree: Path):
    from mas.runtime.registry import register_plugin, unregister_plugin

    urn = "mas.system_plugin.dynamic_system_tool"
    try:
        register_plugin(
            urn,
            _DynamicSystemPlugin,
            shortcuts=["dynamic_system_tool"],
            attributes={"plugin_type": "system_plugin", "default_enabled": False},
        )
        provider = build_manifest_tool_provider(
            [],
            calculator_tool_tree,
            spec={"system_plugins": ["dynamic_system_tool"]},
        )

        assert [tool["function"]["name"] for tool in provider.list_openai_tools()] == ["dynamic_system_tool"]
        assert provider.call_tool("dynamic_system_tool", {}) == "registered"
    finally:
        unregister_plugin(urn)


def test_system_plugin_enabled_false_disables_an_explicit_plugin(calculator_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [],
        calculator_tool_tree,
        spec={"system_plugins": [{"request_human_input": {"enabled": False}}]},
    )

    assert provider.list_openai_tools() == []


def test_spawn_subagent_tool_advertises_only_granted_templates():
    from types import SimpleNamespace

    from mas.library.standard.plugins.system_tools.spawn_subagent import SpawnSubagentTool

    tool = SpawnSubagentTool()
    assert tool.on_collect_tools(ctx=SimpleNamespace()) == []
    assert tool.on_collect_tools(ctx=SimpleNamespace(allow_subagent_spawning=True, subagent_templates=[])) == []
    advertised = tool.on_collect_tools(
        ctx=SimpleNamespace(
            allow_subagent_spawning=True,
            subagent_templates=[{"id": "worker", "description": "Review code"}],
        )
    )
    assert [spec["name"] for spec in advertised] == ["create_subagent"]
    assert advertised[0]["parameters"]["properties"]["template"]["enum"] == ["worker"]


def test_manifest_tool_attachment_gates_spawn_subagent_on_manifest_capability(tmp_path: Path):
    from types import SimpleNamespace

    from mas.runtime.engine.manifest_tool_provider import attach_manifest_tools

    manifest = {
        "spec": {
            "system_plugins": [
                {"subagents": {"templates": [{"id": "worker", "ref": "worker.yaml", "description": "Review"}]}}
            ],
        }
    }
    ctx = SimpleNamespace()
    engine = SimpleNamespace()

    attach_manifest_tools(engine, manifest, tmp_path, ctx=ctx)

    advertised = engine.tool_provider.list_openai_tools(ctx=ctx)
    assert [tool["function"]["name"] for tool in advertised] == ["create_subagent"]
    assert advertised[0]["function"]["parameters"]["properties"]["template"]["enum"] == ["worker"]


def test_manifest_tool_attachment_accepts_create_subagent_shortcut(tmp_path: Path):
    from types import SimpleNamespace

    from mas.runtime.engine.manifest_tool_provider import attach_manifest_tools

    manifest = {
        "spec": {
            "system_plugins": [
                {"create_subagent": {"templates": [{"id": "worker", "ref": "worker.yaml", "description": "Review"}]}}
            ],
        }
    }
    ctx = SimpleNamespace()
    engine = SimpleNamespace()

    attach_manifest_tools(engine, manifest, tmp_path, ctx=ctx)

    advertised = engine.tool_provider.list_openai_tools(ctx=ctx)
    assert [tool["function"]["name"] for tool in advertised] == ["create_subagent"]


def test_manifest_tool_attachment_hides_spawn_without_templates(tmp_path: Path):
    from types import SimpleNamespace

    from mas.runtime.engine.manifest_tool_provider import attach_manifest_tools

    manifest = {"spec": {"system_plugins": ["subagents"]}}
    ctx = SimpleNamespace()
    engine = SimpleNamespace()

    attach_manifest_tools(engine, manifest, tmp_path, ctx=ctx)

    assert engine.tool_provider.list_openai_tools(ctx=ctx) == []


def test_ref_entry_params_override_yaml_impl_params(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "echo_impl.py").write_text(
        """
class EchoImplTool:
    def __init__(self, impl="native", base_dir=None):
        self.impl = impl
        self.base_dir = base_dir

    def on_collect_tools(self, **_):
        return [{"name": "echo_impl", "description": "echo", "parameters": {"type": "object", "properties": {}}}]

    def on_execute_tool(self, name, args, **_):
        if name != "echo_impl":
            return None
        return {"impl": self.impl, "base_dir": self.base_dir}
""",
        encoding="utf-8",
    )
    (tool_dir / "echo_impl.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "echo_impl"},
                "spec": {
                    "impl": {
                        "module_path": "./echo_impl.py",
                        "class_name": "EchoImplTool",
                        "params": {"impl": "native", "base_dir": "./skills"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    provider = build_manifest_tool_provider(
        [
            {
                "ref": "tools/echo_impl.tool.yaml",
                "params": {"impl": "langchain", "base_dir": "./custom"},
            }
        ],
        tmp_path,
    )
    out = provider.call_tool("echo_impl", {})
    assert out == {"impl": "langchain", "base_dir": "./custom"}


def test_openai_tools_uses_provider(calculator_tool_tree: Path):
    manifest = {"spec": {"tools": [{"ref": "tools/calculator.tool.yaml"}]}}
    provider = build_manifest_tool_provider(manifest["spec"]["tools"], calculator_tool_tree)
    tools = openai_tools(manifest, tool_provider=provider)
    assert tools[0]["function"]["name"] == "calculator"


def test_tools_with_resolved_names_deep_copies_when_unchanged():
    from mas.runtime.engine.tools import tools_with_resolved_names

    original = [{"name": "calc"}]
    result = tools_with_resolved_names(original, Path.cwd())
    assert result == original
    assert result is not original
    assert result[0] is not original[0]


def test_tool_name_from_ref_resolves_library_scheme(require_samples_library):
    from mas.runtime.engine.tools import tool_name_from_ref

    assert tool_name_from_ref("samples:tools/calc.tool.yaml", base_dir=Path.cwd()) == "calc"


def test_unknown_bare_tool_name_errors(tmp_path: Path):
    # A bare name resolves via the library tool catalog; a name no library
    # declares errors clearly (not a "bare names unsupported" rejection).
    with pytest.raises(ManifestToolLoadError, match="not found in any library catalog"):
        build_manifest_tool_provider(["definitely-not-a-real-tool"], tmp_path)


def test_unknown_bare_tool_name_reports_local_directory(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    with pytest.raises(ManifestToolLoadError, match="checked local tools directory"):
        build_manifest_tool_provider(
            ["definitely-not-a-real-tool"],
            tmp_path,
            tools_dir=tools_dir,
        )


def test_execute_engine_tool_requires_provider():
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    with pytest.raises(ClassifiedFailure, match="No manifest tool provider") as caught:
        execute_engine_tool("calculator", arguments={"expression": "1"})
    assert caught.value.failure_class is FailureClass.APPLICATION


def test_execute_engine_tool_via_provider(calculator_tool_tree: Path):
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        calculator_tool_tree,
    )
    out = execute_engine_tool(
        "calculator",
        arguments={"expression": "2**16"},
        tool_provider=provider,
    )
    assert "65536" in out


def test_tool_ref_path_traversal_rejected(tmp_path: Path):
    workspace = tmp_path / "workspace" / "app" / "agents"
    workspace.mkdir(parents=True)
    outside = tmp_path.parent / "outside_escape.tool.yaml"
    outside.write_text(
        "kind: Tool\nmetadata: {name: evil}\nspec: {impl: {module_path: ./x.py}}\n",
        encoding="utf-8",
    )
    try:
        with pytest.raises(ManifestToolLoadError, match="path escapes"):
            build_manifest_tool_provider(
                [{"ref": "../../../../outside_escape.tool.yaml"}],
                workspace,
                app_root=tmp_path / "workspace" / "app",
            )
    finally:
        outside.unlink(missing_ok=True)


def test_tool_module_path_traversal_rejected(tmp_path: Path):
    workspace = tmp_path / "workspace" / "app" / "agents"
    workspace.mkdir(parents=True)
    evil = tmp_path.parent / "evil_escape.py"
    evil.write_text("class T:\n    pass\n", encoding="utf-8")
    try:
        with pytest.raises(ManifestToolLoadError, match="path escapes"):
            build_manifest_tool_provider(
                [{"module_path": "../../../../evil_escape.py", "class_name": "T"}],
                workspace,
                app_root=tmp_path / "workspace" / "app",
            )
    finally:
        evil.unlink(missing_ok=True)


def test_wrong_kind_rejected(tmp_path: Path):
    tool_yaml = tmp_path / "agent.tool.yaml"
    tool_yaml.write_text(
        yaml.safe_dump(
            {
                "kind": "Agent",
                "metadata": {"name": "oops"},
                "spec": {"impl": {"module_path": "./x.py"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ManifestToolLoadError, match="kind: Tool"):
        build_manifest_tool_provider([{"ref": "agent.tool.yaml"}], tmp_path)


def test_call_tool_none_result_when_owner_returns_empty(calculator_tool_tree: Path):
    tool_dir = calculator_tool_tree / "tools"
    (tool_dir / "side_effect.py").write_text(
        """
class SideEffectTool:
    def on_collect_tools(self, **_):
        return [{"name": "ping", "description": "ping", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name == "ping":
            return None
        return None
""",
        encoding="utf-8",
    )
    (tool_dir / "ping.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "ping"},
                "spec": {"impl": {"module_path": "./side_effect.py", "class_name": "SideEffectTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/ping.tool.yaml"}],
        calculator_tool_tree,
    )
    assert provider.call_tool("ping", {}) == ""


def test_module_loaded_once_for_same_path(calculator_tool_tree: Path):
    import sys

    provider_a = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        calculator_tool_tree,
    )
    keys_after_first = [k for k in sys.modules if k.startswith("_mas_tool_")]
    provider_b = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        calculator_tool_tree,
    )
    keys_after_second = [k for k in sys.modules if k.startswith("_mas_tool_")]
    assert keys_after_first == keys_after_second
    assert provider_a.list_openai_tools() == provider_b.list_openai_tools()


def test_concurrent_package_tool_load_is_thread_safe(tmp_path: Path):
    import concurrent.futures

    tool_dir = tmp_path / "pkg_tools"
    tool_dir.mkdir()
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "run_action.py").write_text(
        """
class RunActionTool:
    def on_collect_tools(self, **_):
        return [{"name": "run_action", "description": "act", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name != "run_action":
            return None
        return {"ok": True}
""",
        encoding="utf-8",
    )
    (tool_dir / "run_action.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "run_action"},
                "spec": {"impl": {"module_path": "./run_action.py", "class_name": "RunActionTool"}},
            }
        ),
        encoding="utf-8",
    )

    def _load_once(_: int) -> None:
        provider = build_manifest_tool_provider(
            [{"ref": "pkg_tools/run_action.tool.yaml"}],
            tmp_path,
        )
        assert provider.call_tool("run_action", {}) == {"ok": True}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_load_once, range(32)))


def test_package_tool_load_does_not_mutate_sys_path(tmp_path: Path):
    import sys

    tool_dir = tmp_path / "pkg_tools"
    tool_dir.mkdir()
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    (tool_dir / "packaged.py").write_text(
        """
from .helper import VALUE

class PackagedTool:
    def on_collect_tools(self, **_):
        return [{"name": "packaged", "description": "pkg", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name != "packaged":
            return None
        return {"value": VALUE}
""",
        encoding="utf-8",
    )
    (tool_dir / "packaged.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "packaged"},
                "spec": {"impl": {"module_path": "./packaged.py", "class_name": "PackagedTool"}},
            }
        ),
        encoding="utf-8",
    )
    path_before = list(sys.path)
    provider = build_manifest_tool_provider(
        [{"ref": "pkg_tools/packaged.tool.yaml"}],
        tmp_path,
    )
    assert sys.path == path_before
    assert provider.call_tool("packaged", {}) == {"value": 42}


def test_call_tool_propagates_on_collect_tools_error(calculator_tool_tree: Path):
    tool_dir = calculator_tool_tree / "tools"
    (tool_dir / "broken_collect.py").write_text(
        """
class BrokenCollectTool:
    def on_collect_tools(self, **_):
        raise RuntimeError("misconfigured collect")
    def on_execute_tool(self, name, args, **_):
        raise RuntimeError("misconfigured execute")
""",
        encoding="utf-8",
    )
    (tool_dir / "broken.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "broken"},
                "spec": {"impl": {"module_path": "./broken_collect.py", "class_name": "BrokenCollectTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/broken.tool.yaml"}],
        calculator_tool_tree,
    )
    with pytest.raises(RuntimeError, match="misconfigured collect"):
        provider.call_tool("broken", {})


def test_list_tools_propagates_on_collect_tools_error(calculator_tool_tree: Path):
    tool_dir = calculator_tool_tree / "tools"
    (tool_dir / "broken_collect.py").write_text(
        """
class BrokenCollectTool:
    def on_collect_tools(self, **_):
        raise RuntimeError("misconfigured collect")
    def on_execute_tool(self, name, args, **_):
        raise RuntimeError("misconfigured execute")
""",
        encoding="utf-8",
    )
    (tool_dir / "broken.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "broken"},
                "spec": {"impl": {"module_path": "./broken_collect.py", "class_name": "BrokenCollectTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/broken.tool.yaml"}],
        calculator_tool_tree,
    )
    with pytest.raises(RuntimeError, match="misconfigured collect"):
        provider.list_tools()


def test_call_tool_propagates_execute_error_when_owner(calculator_tool_tree: Path):
    tool_dir = calculator_tool_tree / "tools"
    (tool_dir / "broken_exec.py").write_text(
        """
class BrokenExecTool:
    def on_collect_tools(self, **_):
        return [{"name": "boom", "description": "boom", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        raise RuntimeError("execute failed")
""",
        encoding="utf-8",
    )
    (tool_dir / "boom.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "boom"},
                "spec": {"impl": {"module_path": "./broken_exec.py", "class_name": "BrokenExecTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/boom.tool.yaml"}],
        calculator_tool_tree,
    )
    with pytest.raises(RuntimeError, match="execute failed"):
        provider.call_tool("boom", {})


def test_agent_sibling_tools_layout(tmp_path: Path):
    """Refs like ../tools/ from agents/ resolve under app_root containment."""
    app_root = tmp_path / "app"
    agents = app_root / "agents"
    tools = app_root / "tools"
    agents.mkdir(parents=True)
    tools.mkdir()
    (tools / "ping.py").write_text(
        """
class PingTool:
    def on_collect_tools(self, **_):
        return [{"name": "ping", "description": "ping", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"pong": name == "ping"}
""",
        encoding="utf-8",
    )
    (tools / "ping.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "ping"},
                "spec": {"impl": {"module_path": "./ping.py", "class_name": "PingTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "../tools/ping.tool.yaml"}],
        agents,
        app_root=app_root,
    )
    assert provider.call_tool("ping", {}) == {"pong": True}


def test_shared_library_tools_under_app_ancestors(tmp_path: Path):
    """library-samples style: agents/../../../tools under shared library root."""
    library = tmp_path / "library-samples"
    app = library / "apps" / "qa-agent"
    agents = app / "agents"
    shared_tools = library / "tools"
    agents.mkdir(parents=True)
    shared_tools.mkdir(parents=True)
    (shared_tools / "calc.py").write_text(
        """
class CalcTool:
    def on_collect_tools(self, **_):
        return [{"name": "calc", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"ok": name == "calc"}
""",
        encoding="utf-8",
    )
    (shared_tools / "calc.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calc"},
                "spec": {"impl": {"module_path": "./calc.py", "class_name": "CalcTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "../../../tools/calc.tool.yaml"}],
        agents,
        app_root=app,
        workspace_root=tmp_path,
    )
    assert provider.call_tool("calc", {}) == {"ok": True}


def test_library_scheme_tool_ref():
    """Tool refs use manifest library scheme (samples:tools/…) not path traversal."""
    from pathlib import Path

    from mas.runtime.package_refs import resolve_path_ref

    calc_path = resolve_path_ref("samples:tools/calc.tool.yaml", Path.cwd())
    assert calc_path.is_file()
    provider = build_manifest_tool_provider(
        [{"ref": "samples:tools/calc.tool.yaml"}],
        Path(__file__).resolve().parent,
    )
    names = {t["function"]["name"] for t in provider.list_openai_tools()}
    assert "calc" in names


def test_call_tool_skips_non_owner_instances(calculator_tool_tree: Path):
    tool_dir = calculator_tool_tree / "tools"
    (tool_dir / "spy.py").write_text(
        """
class SpyTool:
    def on_collect_tools(self, **_):
        return [{"name": "spy", "description": "spy", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        raise AssertionError("on_execute_tool must not run for non-matching tool calls")
""",
        encoding="utf-8",
    )
    (tool_dir / "spy.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "spy"},
                "spec": {"impl": {"module_path": "./spy.py", "class_name": "SpyTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [
            {"ref": "tools/calculator.tool.yaml"},
            {"ref": "tools/spy.tool.yaml"},
        ],
        calculator_tool_tree,
    )
    out = provider.call_tool("calculator", {"expression": "2**16"})
    assert out["result"] == 65536


def test_duplicate_tool_names_rejected(calculator_tool_tree: Path):
    with pytest.raises(ManifestToolLoadError, match="duplicate"):
        build_manifest_tool_provider(
            [
                {"ref": "tools/calculator.tool.yaml"},
                {"ref": "tools/calculator.tool.yaml"},
            ],
            calculator_tool_tree,
        )


def test_dotted_module_path_imports_installed_package(tmp_path: Path, monkeypatch):
    """Third-party dotted module_path works when the package is on sys.path."""
    pkg_root = tmp_path / "site-packages"
    pkg_dir = pkg_root / "myorg" / "travel" / "tools"
    pkg_dir.mkdir(parents=True)
    for part in (pkg_root / "myorg", pkg_root / "myorg" / "travel", pkg_dir):
        (part / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "checker.py").write_text(
        """
class CheckerTool:
    def on_collect_tools(self, **_):
        return [{"name": "check", "description": "c", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"ok": name == "check"}
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(pkg_root))
    provider = build_manifest_tool_provider(
        [{"module_path": "myorg.travel.tools.checker", "class_name": "CheckerTool"}],
        tmp_path,
    )
    assert provider.call_tool("check", {}) == {"ok": True}


def test_deep_app_tree_tools_under_workspace(tmp_path: Path):
    """apps/category/<app>/agents can reach workspace-level shared tools."""
    workspace = tmp_path / "workspace"
    app = workspace / "apps" / "category" / "myapp"
    agents = app / "agents"
    external_tools = workspace / "shared-tools"
    agents.mkdir(parents=True)
    external_tools.mkdir(parents=True)
    (external_tools / "ping.py").write_text(
        """
class PingTool:
    def on_collect_tools(self, **_):
        return [{"name": "ping", "description": "ping", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"pong": name == "ping"}
""",
        encoding="utf-8",
    )
    (external_tools / "ping.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "ping"},
                "spec": {"impl": {"module_path": "./ping.py", "class_name": "PingTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "../../../../shared-tools/ping.tool.yaml"}],
        agents,
        app_root=app,
        workspace_root=workspace,
    )
    assert provider.call_tool("ping", {}) == {"pong": True}
