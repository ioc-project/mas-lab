#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Topology plot: layout and drawing for arbitrary delegation graphs, peers and cycles."""

from __future__ import annotations

import xml.dom.minidom

import pytest
from mas.lab.plots.topology import Agent, Topology, _columns, load_topology, plot_topology


def _topo(edges, peers=(), entry="a", names="abcdef"):
    agents = {k: Agent(k, ["s1", "s2"], ["t1"]) for k in names}
    return Topology(agents, list(edges), list(peers), entry)


def test_columns_follow_delegation_depth():
    cols = _columns(_topo([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")], names="abcd"))
    assert cols == [["a"], ["b", "c"], ["d"]]


def test_cycle_does_not_inflate_columns():
    cols = _columns(_topo([("a", "b"), ("b", "c"), ("c", "b")], names="abc"))
    assert cols == [["a"], ["b"], ["c"]]


def test_unreachable_agent_is_still_drawn():
    svg = plot_topology(_topo([("a", "b")], names="abz"))
    assert 'data-id="z"' in svg


def test_delegate_order_is_kept():
    cols = _columns(_topo([("a", "z"), ("a", "m"), ("a", "b")], names="azmb"))
    assert cols[1] == ["z", "m", "b"]


def test_peer_edges_are_drawn_and_legended():
    svg = plot_topology(_topo([("a", "b"), ("a", "c")], peers=[("b", "c")], names="abc"))
    assert 'class="edge peer"' in svg and "talks to (peer)" in svg


def test_svg_is_well_formed_and_html_wraps_it():
    topo = _topo([("a", "b"), ("b", "c"), ("c", "a")], peers=[("a", "c")], names="abc")
    xml.dom.minidom.parseString(plot_topology(topo))
    assert plot_topology(topo, fmt="html").startswith("<!doctype html>")
    with pytest.raises(ValueError):
        plot_topology(topo, fmt="png")


def test_load_topology_reads_specs(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "x.yaml").write_text(
        "spec:\n  skills: [alpha, {name: beta}]\n  tools:\n    - ref: ../tools/run.tool.yaml\n"
    )
    (tmp_path / "mas.yaml").write_text(
        "metadata: {name: demo}\nspec:\n  agency:\n    agents:\n      - {id: x, ref: agents/x.yaml}\n      - {id: y}\n"
        "  workflow:\n    entry: x\n    nodes:\n      - {id: x, delegates_to: [y], peers: [y]}\n"
    )
    topo = load_topology(tmp_path / "mas.yaml")
    assert topo.agents["x"].skills == ["alpha", "beta"] and topo.agents["x"].tools == ["run"]
    assert topo.delegations == [("x", "y")] and topo.peers == [("x", "y")] and topo.name == "demo"
