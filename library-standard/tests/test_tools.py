#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest tool loading via Tool YAML refs."""

from pathlib import Path

import pytest
import yaml
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
from mas.runtime.engine.tool_dispatch import execute_engine_tool


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
    from mas.library.standard.plugins.tools.local import LocalToolProvider

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
