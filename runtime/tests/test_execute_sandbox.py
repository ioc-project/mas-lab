#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path

from mas.library.standard.plugins.sandbox.workdir import WorkdirSandbox
from mas.runtime.boundary.sandbox import sandbox_from_name
from mas.runtime.engine.tool_dispatch import execute_engine_tool


class _CwdTool:
    def claims(self, tool_name: str) -> bool:
        return tool_name == "cwd"

    def call(self, tool_name: str, arguments: dict, **_: object) -> str:
        marker = Path("sandbox-marker.txt")
        marker.write_text("inside")
        return os.getcwd()


def test_workdir_sandbox_is_the_body_of_allow(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    host = Path.cwd()
    out = execute_engine_tool(
        "cwd",
        engine_contracts=(_CwdTool(),),
        sandbox=WorkdirSandbox(root),
    )
    assert Path(out) == root.resolve()
    assert (root / "sandbox-marker.txt").read_text() == "inside"
    assert not (host / "sandbox-marker.txt").exists()
    assert Path.cwd() == host


def test_mcp_style_tool_provider_still_runs_inside_sandbox() -> None:
    """MCP (and any tool_provider) must hit execute_engine_tool, hence the sandbox."""

    class _McpProvider:
        def call_tool(self, name, arguments, **_kw):
            return {"ok": True, "tool": name, "args": arguments}

    class _RecordingBox:
        def __init__(self) -> None:
            self.seen: str | None = None

        def run(self, body, *, tool_name=""):
            self.seen = tool_name
            return body()

    box = _RecordingBox()
    out = execute_engine_tool(
        "mcp_echo",
        arguments={"text": "hi"},
        tool_provider=_McpProvider(),
        sandbox=box,
    )
    assert box.seen == "mcp_echo"
    assert "mcp_echo" in out


def test_sandbox_from_name_passthrough() -> None:
    box = sandbox_from_name("none")
    assert box.run(lambda: 7) == 7
