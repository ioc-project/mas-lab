#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unknown cities are an explicit error, not a zero-hop route.

Load the sample tools by file path. Several trees ship a same-named ``tools``
package; a bare ``sys.path`` import would resolve to whichever one pytest
imported first.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DATASET = str(ROOT / "fixtures" / "arborian-network.yaml")
_PKG = "library_samples_trip_tools"


def _mod(name: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(TOOLS)]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    if name != "_scene" and f"{_PKG}._scene" not in sys.modules:
        _mod("_scene")
    spec = importlib.util.spec_from_file_location(full, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_query_graph_unknown_city_same_origin_and_destination() -> None:
    tool = _mod("query_graph_database").QueryGraphDatabaseTool(dataset_path=DATASET)
    result = tool.call_tool(
        "query_graph_database",
        {"origin": "Thornhaven", "destination": "Thornhaven"},
    )
    assert result["found"] is False
    assert result["error"] == "unknown_city"
    assert result["unknown"] == ["Thornhaven"]
    assert "routes" not in result


def test_lookup_schedule_unknown_city() -> None:
    tool = _mod("lookup_schedule").LookupScheduleTool(dataset_path=DATASET)
    result = tool.call_tool(
        "lookup_schedule",
        {"origin": "Celestia", "destination": "Thornhaven"},
    )
    assert result["found"] is False
    assert result["error"] == "unknown_city"
    assert result["unknown"] == ["Thornhaven"]


def test_known_city_same_origin_and_destination_is_zero_hop() -> None:
    tool = _mod("query_graph_database").QueryGraphDatabaseTool(dataset_path=DATASET)
    result = tool.call_tool(
        "query_graph_database",
        {"origin": "Celestia", "destination": "Celestia"},
    )
    assert result["found"] is True
    assert result["routes"][0]["total_hops"] == 0
