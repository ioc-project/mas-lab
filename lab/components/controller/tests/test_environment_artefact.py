#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.lab.controller.ui_stream.artefact_bind import LIVE_GRAPH, SHORTCUTS, canonical_artefact


def test_environment_shortcut_and_live_graph() -> None:
    assert SHORTCUTS["environment"] == "custom.environment"
    assert canonical_artefact("environment") == "custom.environment"
    node = LIVE_GRAPH["custom.environment"]
    assert node["stream"] == "custom.environment"
    assert node["plot"] == "timeline"
