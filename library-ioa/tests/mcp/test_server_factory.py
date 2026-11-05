from __future__ import annotations

from pathlib import Path

from library_ioa.plugins.mcp.server import MCPToolServerFactory


def test_factory_wraps_manifest_tool(tmp_path: Path) -> None:
    tool_module = tmp_path / "sample_tool.py"
    tool_module.write_text(
        "class SampleTool:\n    def execute(self, **kwargs):\n        return {'answer': kwargs['a'] + kwargs['b']}\n",
        encoding="utf-8",
    )

    manifest_path = tmp_path / "sample.tool.yaml"
    manifest_path.write_text(
        "apiVersion: mas/v1\n"
        "kind: Tool\n"
        "metadata:\n"
        "  name: sample-tool\n"
        "  description: Sample tool\n"
        "spec:\n"
        "  description: Adds two integers\n"
        "  parameters:\n"
        "    - name: a\n"
        "      type: integer\n"
        "      required: true\n"
        "    - name: b\n"
        "      type: integer\n"
        "      required: true\n"
        "  returns:\n"
        "    type: integer\n"
        "  impl:\n"
        "    kind: python\n"
        f"    module_path: {tool_module.name}\n"
        "    class_name: SampleTool\n",
        encoding="utf-8",
    )

    factory = MCPToolServerFactory.from_manifest(manifest_path)

    assert len(factory._tools) == 1
    assert factory._tools[0]["name"] == "sample-tool"
    assert factory._tools[0]["fn"](a=2, b=3) == {"answer": 5}
