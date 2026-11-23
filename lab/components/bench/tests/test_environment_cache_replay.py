#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import json
from pathlib import Path

from mas.lab.benchmark.environment.attach import replay_environment_jsonl


def test_cache_hit_re_emits_environment_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "environment.jsonl"
    path.parent.mkdir(parents=True)
    record = {"v": 1, "seq": 1, "name": "clock.started", "kind": "clock"}
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    seen: list[dict] = []
    replay_environment_jsonl(path, seen.append)
    assert seen[0]["name"] == "clock.started"
    assert seen[0]["kind"] == "clock"


def test_recorded_replay_redelivers_at_anchor(tmp_path: Path) -> None:
    from mas.lab.benchmark.environment.engine import RunEnvironment
    from mas.lab.benchmark.environment.model import RunCoordinates, compile_environment

    path = tmp_path / "traces" / "environment.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "name": "event.emitted",
                "anchor": {
                    "hook": "ingress",
                    "op": "TOOL_RESULT",
                    "tool": "pay_fare",
                    "agent": "",
                    "occurrence": 2,
                },
                "payload": {"event": "user.tick"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = RunEnvironment(
        compile_environment({"replay": "recorded"}),
        run=RunCoordinates(),
        path=path,
    )

    class A:
        hook = "ingress"
        op = "TOOL_RESULT"
        tool = "pay_fare"
        agent = ""
        occurrence = 2

    env.on_transition("tool_result", tool="pay_fare", hook="ingress", op="TOOL_RESULT", anchor=A())
    names = [r["name"] for r in env.events.records()]
    assert "event.delivered" in names
    delivered = next(r for r in env.events.records() if r["name"] == "event.delivered")
    assert delivered["payload"]["event"] == "user.tick"
