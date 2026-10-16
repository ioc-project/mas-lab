#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Sample tools implement ``ToolContract`` (``list_tools`` + ``call_tool`` or ``execute``).

``call_tool`` is the one required invocation: the MCP server, the A2A bridge and the
registry all use it. A tool that only overrides the ``on_*`` hooks works for the local
provider and breaks for every other caller.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _tool_classes():
    for path in sorted(TOOLS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                getattr(base, "id", "") == "ToolContract" for base in node.bases
            ):
                methods = {m.name for m in node.body if isinstance(m, ast.FunctionDef)}
                yield pytest.param(path.name, node.name, methods, id=f"{path.name}:{node.name}")


@pytest.mark.parametrize(("module", "cls", "methods"), _tool_classes())
def test_tool_uses_the_contract_not_the_hooks(module, cls, methods):
    assert not methods & {"on_execute_tool", "on_collect_tools"}, f"{cls} overrides a hook"
    assert methods & {"call_tool", "execute"}, f"{cls} has no call_tool/execute"


def test_calc_serves_through_call_tool():
    spec = importlib.util.spec_from_file_location("sample_calc", TOOLS / "calc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tool = module.CalcTool()
    assert [t["name"] for t in tool.list_tools()] == ["calc"]
    assert tool.call_tool("calc", {"expression": "14 / 2.1"}) == {"expression": "14 / 2.1", "result": 6.67}
    # the runtime hooks reach the same code
    assert tool.on_execute_tool("calc", {"expression": "1 + 1"})["result"] == 2.0
