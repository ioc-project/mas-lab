#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.environment.engine import RunEnvironment
from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment


def test_observability_delivered_at_next_boundary() -> None:
    spec = compile_environment(
        {
            "triggers": [
                {
                    "id": "obs",
                    "when": {"on": "observability", "match": {"kind": "tool_result"}},
                    "do": [{"type": "emit", "config": {"name": "user.saw-obs"}}],
                }
            ]
        }
    )
    env = RunEnvironment(spec, run=RunCoordinates())
    env.note_observability({"kind": "tool_result"})
    assert not any(r["name"] == "user.saw-obs" for r in env.events.records())

    class A:
        hook = "ingress"
        op = "TOOL_RESULT"
        tool = ""
        agent = ""
        occurrence = 1

    env.on_transition("tool_result", anchor=A())
    names = [r["name"] for r in env.events.records()]
    assert "user.saw-obs" in names
