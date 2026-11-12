#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest tool loading via Tool YAML refs."""

import asyncio
from pathlib import Path

import pytest
import yaml
from mas.library.standard.plugins.tools.local import LocalToolProvider
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
from mas.runtime.engine.tool_dispatch import execute_engine_tool
from mas.runtime.registry.provider_protocol import ManifestToolLoadError


def _provider_with_calculator(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calculator.py").write_text(
        """
import re

class CalculatorTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        expr = str(args.get("expression") or "")
        if re.search(r"17\\s*[×x*]\\s*23", expr):
            return {"result": 391}
        return {"result": 65536}
""",
        encoding="utf-8",
    )
    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {"impl": {"module_path": "./calculator.py", "class_name": "CalculatorTool"}},
            }
        ),
        encoding="utf-8",
    )
    return build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        tmp_path,
    )


def test_calculator_via_manifest_provider(tmp_path: Path):
    provider = _provider_with_calculator(tmp_path)
    out = execute_engine_tool(
        "calculator",
        user="17 × 23",
        arguments={"expression": "17*23"},
        tool_provider=provider,
    )
    assert "391" in out


def test_web_search_via_manifest_provider(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "web_search.py").write_text(
        """
class WebSearchTool:
    def on_collect_tools(self, **_):
        return [{"name": "web-search", "description": "search", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        return {"query": args.get("query"), "results": []}
""",
        encoding="utf-8",
    )
    (tool_dir / "web-search.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "web-search"},
                "spec": {"impl": {"module_path": "./web_search.py", "class_name": "WebSearchTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/web-search.tool.yaml"}],
        tmp_path,
    )
    out = execute_engine_tool(
        "web-search",
        arguments={"query": "test query"},
        tool_provider=provider,
    )
    assert "test query" in out


def test_memory_search_via_manifest_provider(tmp_path: Path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "memory_search.py").write_text(
        """
class MemorySearchTool:
    def on_collect_tools(self, **_):
        return [{
            "name": "memory-search",
            "description": "search memory",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }]
    def on_execute_tool(self, name, args, **_):
        if name != "memory-search":
            return None
        return {"query": args.get("query"), "items": [], "text": "no matches"}
""",
        encoding="utf-8",
    )
    (tool_dir / "memory-search.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "memory-search"},
                "spec": {
                    "impl": {
                        "module_path": "./memory_search.py",
                        "class_name": "MemorySearchTool",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/memory-search.tool.yaml"}],
        tmp_path,
    )
    out = execute_engine_tool(
        "memory-search",
        arguments={"query": "Paris trip"},
        tool_provider=provider,
    )
    assert "Paris trip" in out


def test_local_execute_failure_propagates():
    class Boom:
        def on_collect_tools(self, **_):
            return [{"name": "boom"}]

        def on_execute_tool(self, name, args, **_):
            raise RuntimeError("nope")

    provider = LocalToolProvider()
    provider._add_instance(Boom(), {"name": "boom"})
    with pytest.raises(RuntimeError, match="nope"):
        provider.call_tool("boom", {})


def test_from_tool_servers_skips_non_local_and_keeps_the_last_local() -> None:
    from mas.library.standard.plugins.tools.local import LocalToolProvider

    assert LocalToolProvider.from_tool_servers([]) == []
    assert LocalToolProvider.from_tool_servers([{"protocol": "mcp"}]) == []
    providers = LocalToolProvider.from_tool_servers(
        [
            "skip",
            {"id": "local-first", "protocol": "local", "tools": ["calc"]},
            {"id": "local-last", "protocol": "LOCAL", "tools": ["search"]},
        ]
    )
    assert len(providers) == 1
    assert providers[0].provider_name == "local-last"
    assert providers[0].tools_claim == ("search",)


class _AsyncEcho:
    def on_collect_tools(self, **_):
        return [{"name": "echo"}]

    async def on_aexecute_tool(self, name, args, **_):
        await asyncio.sleep(0)
        return {"async": name, **args}

    def on_execute_tool(self, name, args, **_):
        raise AssertionError("sync on_execute_tool must not run when on_aexecute_tool is present")


class _NoneAsync:
    def on_collect_tools(self, **_):
        return [{"name": "empty"}]

    def on_aexecute_tool(self, name, args, **_):
        return None


class _LegacySync:
    def on_collect_tools(self, **_):
        raise NotImplementedError

    def get_name(self):
        return "legacy"

    def on_execute_tool(self, name, args, **_):
        return {"sync": name}


class _Blind:
    def on_collect_tools(self, **_):
        return [{"name": "other"}]

    def get_name(self):
        raise RuntimeError("no name")


@pytest.mark.asyncio
async def test_acall_tool_prefers_on_aexecute_tool():
    provider = LocalToolProvider()
    provider._add_instance(_AsyncEcho(), {"name": "echo"})
    out = await provider.acall_tool("echo", {"q": 1})
    assert out == {"async": "echo", "q": 1}


@pytest.mark.asyncio
async def test_acall_tool_none_from_on_aexecute_tool_is_empty_string():
    provider = LocalToolProvider()
    provider._add_instance(_NoneAsync(), {"name": "empty"})
    assert await provider.acall_tool("empty", {}) == ""


@pytest.mark.asyncio
async def test_acall_tool_runs_sync_owner_in_a_worker():
    provider = LocalToolProvider()
    provider._add_instance(_LegacySync(), {"name": "legacy"})
    assert await provider.acall_tool("legacy", {}) == {"sync": "legacy"}


@pytest.mark.asyncio
async def test_acall_tool_falls_back_when_no_owner():
    provider = LocalToolProvider()
    provider._add_instance(_Blind(), {"name": "other"})
    with pytest.raises(ManifestToolLoadError, match="not found"):
        await provider.acall_tool("missing", {})


@pytest.mark.asyncio
async def test_acall_tool_system_plugin_uses_call_tool():
    class _Sys:
        def claims(self, name):
            return name == "sys"

        def call(self, name, arguments, context=None):
            return {"via": "system", "name": name, "args": arguments}

    provider = LocalToolProvider()
    provider.system_plugins = _Sys()
    assert await provider.acall_tool("sys", {"k": 2}) == {"via": "system", "name": "sys", "args": {"k": 2}}
