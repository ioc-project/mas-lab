#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.lab.plots.html_demo import SUPPORTED_KINDS
from mas.lab.plots.live_html import d3_inline
from mas.lab.plots.providers import (
    load_plot_providers,
    plot_info,
    render_draw_module,
    render_embedded,
    render_plot,
)


def test_mas_lab_ships_chat_and_topology_only() -> None:
    assert SUPPORTED_KINDS == frozenset({"chat", "topology"})
    kinds = set()
    for provider in load_plot_providers(refresh=True):
        kinds.update(provider.kinds)
        for kind in provider.kinds:
            spec = render_embedded(kind)
            assert "masDraw" in spec["js"]
            assert "<iframe" not in spec["js"].lower()
            assert "postMessage" not in spec["js"]
            module = render_draw_module(kind)
            assert "export default function install" in module
            assert "jsdelivr" not in module
    assert {"chat", "topology"} <= kinds
    assert plot_info("chat")["stream"] == "custom.chat"
    html = render_plot("topology", {}, fmt="html")
    assert "drawTopology" in str(html)
    assert "postMessage" not in str(html)
    assert "jsdelivr" not in d3_inline()
