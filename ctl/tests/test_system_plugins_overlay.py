#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.ctl.overlay.merge import merge_overlay


def test_overlay_add_and_remove_system_plugins_by_id() -> None:
    base = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "desk"},
        "spec": {"description": "d", "system_plugins": ["inform_user", "request_human_input"]},
    }
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "patch"},
        "spec": {
            "target": {"kind": "Agent"},
            "patch": {
                "system_plugins": {
                    "$op": {
                        "add": ["pause_session"],
                        "remove": ["inform_user"],
                    }
                }
            },
        },
    }
    merged = merge_overlay(base, overlay)
    names = merged["spec"]["system_plugins"]
    assert "inform_user" not in names
    assert "request_human_input" in names
    assert "pause_session" in names
