#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 02 — Creating a Multi-Agent System: integration tests.

Tests MAS manifests, overlay topologies, all trip-planner tools (real data
from arborian-network.yaml), and mocked MAS execution.
"""

from __future__ import annotations

import sys

import pytest
import yaml
from conftest import T02, load_yaml, run_cli

# Ensure the tutorial tools directory is importable
sys.path.insert(0, str(T02))


# ═══════════════════════════════════════════════════════════════════════════
# 1. Manifest validation (CLI)
# ═══════════════════════════════════════════════════════════════════════════


class TestMASValidation:
    """mas-ctl validate must pass for all manifests."""

    def test_validate_mas_yaml(self):
        r = run_cli(["mas-ctl", "validate", str(T02 / "mas.yaml")])
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize(
        "agent_dir",
        [
            "moderator",
            "schedule-agent",
            "itinerary-agent",
            "concierge-agent",
            "generalist",
        ],
    )
    def test_validate_agent_manifests(self, agent_dir):
        agent_yaml = T02 / "agents" / agent_dir / "agent.yaml"
        if not agent_yaml.exists():
            pytest.skip(f"{agent_dir}/agent.yaml not present")
        r = run_cli(["mas-ctl", "validate", str(agent_yaml)])
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("overlay", ["single-agent.yaml", "linear.yaml"])
    def test_patch_overlays_well_formed(self, overlay):
        """Verify Patch overlays have correct structure (not validated via CLI)."""
        ov = load_yaml(T02 / "overlays" / overlay)
        assert ov.get("kind") == "Overlay"
        assert ov.get("apiVersion") == "mas/v1"
        assert "spec" in ov
        assert "patch" in ov["spec"]
        assert "target" in ov["spec"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. MAS manifest structure (Python)
# ═══════════════════════════════════════════════════════════════════════════


class TestMASStructure:
    """Verify the MAS manifest and agent manifests have correct shape."""

    def test_mas_yaml_structure(self):
        m = load_yaml(T02 / "mas.yaml")
        assert m["apiVersion"] == "mas/v1"
        assert m["kind"] == "MAS"
        spec = m["spec"]
        assert "agency" in spec
        assert len(spec["agency"]["agents"]) >= 4
        assert "workflow" in spec
        wf = spec["workflow"]
        assert wf["entry"] == "moderator"

    def test_overlays_directory_complete(self):
        """Tutorial 2 is CLI-first (interactive MAS run).
        Benchmarks and datasets are introduced in tutorial 3."""
        expected = ["single-agent.yaml", "linear.yaml"]
        for name in expected:
            p = T02 / "overlays" / name
            assert p.exists(), f"overlay {name} missing from tuto 02"

    def test_overlay_single_agent_structure(self):
        ov = load_yaml(T02 / "overlays" / "single-agent.yaml")
        assert ov["kind"] == "Overlay"
        patch = ov["spec"]["patch"]
        assert patch["agents"]["$op"]["replace"] == [
            {"id": "generalist", "ref": "agents/generalist/agent.yaml"}
        ]
        assert "workflow" in patch
        assert patch["workflow"]["entry"] == "generalist"

    def test_overlay_linear_structure(self):
        ov = load_yaml(T02 / "overlays" / "linear.yaml")
        assert ov["kind"] == "Overlay"
        patch = ov["spec"]["patch"]
        assert "nodes" in patch["workflow"]
        assert patch["workflow"]["entry"] == "moderator"
        assert patch["agents"]["moderator"]["design_pattern"]["type"] == "deterministic_linear"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Arborian Network dataset (Python — data fixture)
# ═══════════════════════════════════════════════════════════════════════════


class TestArborianNetwork:
    """Verify the trip-planner dataset fixture loads correctly."""

    def test_dataset_loads(self):
        ds_path = T02 / "datasets" / "arborian-network.yaml"
        assert ds_path.exists(), "arborian-network.yaml missing"
        with open(ds_path) as f:
            data = yaml.safe_load(f)
        assert "cities" in data
        assert "routes" in data
        assert len(data["cities"]) >= 3
        assert len(data["routes"]) >= 3

    def test_cities_have_highlights(self):
        with open(T02 / "datasets" / "arborian-network.yaml") as f:
            data = yaml.safe_load(f)
        for city in data["cities"]:
            assert "name" in city
            # At least some cities have highlights
        cities_with_highlights = [c for c in data["cities"] if c.get("highlights")]
        assert len(cities_with_highlights) > 0

    def test_routes_have_fares(self):
        with open(T02 / "datasets" / "arborian-network.yaml") as f:
            data = yaml.safe_load(f)
        routes_with_fares = [r for r in data["routes"] if r.get("fares_usd")]
        assert len(routes_with_fares) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Trip-planner tools (Python — real execution against dataset)
# ═══════════════════════════════════════════════════════════════════════════


class TestCalcTool:
    """Test the calculator tool."""

    def test_collect_tools(self):
        from tools.calc import CalcTool

        tool = CalcTool()
        defs = tool.on_collect_tools()
        assert len(defs) == 1
        assert defs[0]["name"] == "calc"

    def test_basic_arithmetic(self):
        from tools.calc import CalcTool

        tool = CalcTool()
        result = tool.on_execute_tool("calc", {"expression": "100 + 200 * 3"})
        assert result["result"] == 700.0

    def test_division(self):
        from tools.calc import CalcTool

        tool = CalcTool()
        result = tool.on_execute_tool("calc", {"expression": "500 / 4"})
        assert result["result"] == 125.0

    def test_invalid_expression(self):
        from tools.calc import CalcTool

        tool = CalcTool()
        result = tool.on_execute_tool("calc", {"expression": "import os"})
        assert "error" in result

    def test_wrong_tool_name_returns_none(self):
        from tools.calc import CalcTool

        tool = CalcTool()
        assert tool.on_execute_tool("other", {}) is None


class TestLookupScheduleTool:
    """Test schedule lookup against arborian-network.yaml."""

    def _make_tool(self):
        from tools.lookup_schedule import LookupScheduleTool

        return LookupScheduleTool(dataset_path=str(T02 / "datasets" / "arborian-network.yaml"))

    def test_collect_tools(self):
        tool = self._make_tool()
        defs = tool.on_collect_tools()
        assert len(defs) == 1
        assert defs[0]["name"] == "lookup_schedule"

    def test_valid_route(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "lookup_schedule",
            {
                "origin": "Celestia",
                "destination": "Verdantia",
            },
        )
        assert result["found"] is True
        assert len(result["routes"]) > 0

    def test_invalid_route(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "lookup_schedule",
            {
                "origin": "Nowhere",
                "destination": "Neverland",
            },
        )
        assert result["found"] is False
        assert result["error"] == "unknown_city"
        assert "Nowhere" in result["unknown"]
        assert "Neverland" in result["unknown"]

    def test_unknown_city_is_not_a_missing_route(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "lookup_schedule",
            {"origin": "Celestia", "destination": "Thornhaven"},
        )
        assert result["found"] is False
        assert result["error"] == "unknown_city"
        assert result["unknown"] == ["Thornhaven"]

    def test_weekend_departures(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "lookup_schedule",
            {
                "origin": "Celestia",
                "destination": "Verdantia",
                "departure_date": "weekend",
            },
        )
        assert result["found"] is True


class TestQueryGraphDatabaseTool:
    """Test graph pathfinding against arborian-network.yaml."""

    def _make_tool(self):
        from tools.query_graph_database import QueryGraphDatabaseTool

        return QueryGraphDatabaseTool(dataset_path=str(T02 / "datasets" / "arborian-network.yaml"))

    def test_collect_tools(self):
        tool = self._make_tool()
        defs = tool.on_collect_tools()
        assert len(defs) == 1
        assert defs[0]["name"] == "query_graph_database"

    def test_direct_route(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "query_graph_database",
            {
                "origin": "Celestia",
                "destination": "Verdantia",
            },
        )
        assert result["found"] is True
        assert len(result["routes"]) > 0
        # Each route should have hops
        for route in result["routes"]:
            assert "hops" in route
            assert route["total_hops"] > 0

    def test_optimise_for_time(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "query_graph_database",
            {
                "origin": "Celestia",
                "destination": "Verdantia",
                "optimise_for": "time",
            },
        )
        assert result["found"] is True
        assert result["optimise_for"] == "time"

    def test_no_path(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "query_graph_database",
            {
                "origin": "Celestia",
                "destination": "NonExistentCity",
            },
        )
        assert result["found"] is False
        assert result["error"] == "unknown_city"
        assert result["unknown"] == ["NonExistentCity"]

    def test_unknown_city_same_origin_and_destination(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "query_graph_database",
            {"origin": "Thornhaven", "destination": "Thornhaven"},
        )
        assert result["found"] is False
        assert result["error"] == "unknown_city"
        assert result["unknown"] == ["Thornhaven"]
        assert "routes" not in result

    def test_known_city_same_origin_and_destination_is_zero_hop(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "query_graph_database",
            {"origin": "Celestia", "destination": "Celestia"},
        )
        assert result["found"] is True
        assert result["routes"][0]["total_hops"] == 0


class TestGetFaresTool:
    """Test fare lookup against arborian-network.yaml."""

    def _make_tool(self):
        from tools.get_fares import GetFaresTool

        return GetFaresTool(dataset_path=str(T02 / "datasets" / "arborian-network.yaml"))

    def test_collect_tools(self):
        tool = self._make_tool()
        defs = tool.on_collect_tools()
        assert len(defs) == 1
        assert defs[0]["name"] == "get_fares"

    def test_valid_fare(self):
        tool = self._make_tool()
        # First find a valid route_id from the dataset
        with open(T02 / "datasets" / "arborian-network.yaml") as f:
            data = yaml.safe_load(f)
        route = next(r for r in data["routes"] if r.get("fares_usd"))
        route_id = route["id"]
        travel_class = list(route["fares_usd"].keys())[0]
        result = tool.on_execute_tool(
            "get_fares",
            {
                "route_id": route_id,
                "travel_class": travel_class,
            },
        )
        assert "fare_usd" in result
        assert isinstance(result["fare_usd"], (int, float))

    def test_invalid_route_id(self):
        tool = self._make_tool()
        result = tool.on_execute_tool(
            "get_fares",
            {
                "route_id": "NONEXISTENT",
                "travel_class": "Standard",
            },
        )
        assert "error" in result

    def test_invalid_travel_class(self):
        tool = self._make_tool()
        with open(T02 / "datasets" / "arborian-network.yaml") as f:
            data = yaml.safe_load(f)
        route = next(r for r in data["routes"] if r.get("fares_usd"))
        result = tool.on_execute_tool(
            "get_fares",
            {
                "route_id": route["id"],
                "travel_class": "UltraLuxury",
            },
        )
        assert "error" in result
        assert "available_classes" in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. Overlay merging (Python)
# ═══════════════════════════════════════════════════════════════════════════


class TestMASOverlayMerging:
    """Test overlay merging produces valid topology changes."""

    def test_single_agent_overlay(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T02 / "mas.yaml")
        overlay = load_yaml(T02 / "overlays" / "single-agent.yaml")
        merged = merge_overlay(base, overlay)
        merged_str = yaml.dump(merged)
        assert "generalist" in merged_str

    def test_linear_overlay(self):
        from mas.ctl.overlay import merge_overlay

        base = load_yaml(T02 / "mas.yaml")
        overlay = load_yaml(T02 / "overlays" / "linear.yaml")
        merged = merge_overlay(base, overlay)
        merged_str = yaml.dump(merged)
        assert "schedule_agent" in merged_str


# ═══════════════════════════════════════════════════════════════════════════
# 6. Skills presence (Python)
# ═══════════════════════════════════════════════════════════════════════════


class TestSkillsPresence:
    """Verify tutorial skills directories exist and have SKILL.md."""

    @pytest.mark.parametrize(
        "skill",
        [
            "fare-and-itinerary-assembly",
            "route-planning",
            "transport-schedule-lookup",
            "trip-orchestration",
        ],
    )
    def test_skill_has_readme(self, skill):
        skill_dir = T02 / "skills" / skill
        assert skill_dir.exists(), f"Skill dir {skill} missing"
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists(), f"SKILL.md missing for {skill}"
        content = skill_md.read_text(encoding="utf-8")
        assert len(content) > 10, f"SKILL.md for {skill} is too short"
        assert content.startswith("---"), f"SKILL.md for {skill} must begin with YAML frontmatter"
        from mas.library.skills.lib.frontmatter import parse_skill_frontmatter

        meta, body = parse_skill_frontmatter(content)
        assert meta.get("name") == skill
        description = meta.get("description") or ""
        assert description
        assert "activate_skill" in description
        assert len(body) > 10
