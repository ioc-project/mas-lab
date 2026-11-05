#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Replay speed and realtime flags for native → OTel replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mas.library.telemetry.conversion.replay import _replay_delay_s, replay_events_file

pytest.importorskip("opentelemetry.sdk")


def test_replay_delay_instant_and_speed_factor() -> None:
    assert _replay_delay_s(1.0, 3.0, 0) == 0.0
    assert _replay_delay_s(1.0, 3.0, -1) == 0.0
    assert _replay_delay_s(1.0, 3.0, 1) == 2.0
    assert _replay_delay_s(1.0, 3.0, 2) == 1.0
    assert _replay_delay_s(None, 3.0, 1) == 0.0
    assert _replay_delay_s(3.0, 1.0, 1) == 0.0


def test_replay_speed_sleeps_scaled_gaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(
        "mas.library.telemetry.conversion.replay.time.sleep",
        lambda delay: slept.append(delay),
    )
    events = [
        {"kind": "execution_start", "call_id": "a1", "agent_id": "planner", "timestamp": 10.0},
        {"kind": "execution_end", "call_id": "a1", "agent_id": "planner", "timestamp": 12.0},
    ]
    src = tmp_path / "events.jsonl"
    src.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    replay_events_file(src, tmp_path / "out.jsonl", app_name="test-app", replay_speed=2, converter_profile="raw")
    assert slept == [1.0]


def test_replay_speed_zero_is_instant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(_delay: float) -> None:
        raise AssertionError("instant replay must not sleep")

    monkeypatch.setattr("mas.library.telemetry.conversion.replay.time.sleep", _fail)
    events = [
        {"kind": "execution_start", "call_id": "a1", "agent_id": "planner", "timestamp": 10.0},
        {"kind": "execution_end", "call_id": "a1", "agent_id": "planner", "timestamp": 12.0},
    ]
    src = tmp_path / "events.jsonl"
    src.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    replay_events_file(src, tmp_path / "out.jsonl", app_name="test-app", replay_speed=0, converter_profile="raw")


def test_realtime_replay_skips_graph_and_emits_signals(tmp_path: Path) -> None:
    events = [
        {"kind": "system_specification", "agents": [{"id": "planner"}], "app_name": "demo"},
        {"kind": "execution_start", "call_id": "a1", "agent_id": "planner", "timestamp": 1.0},
        {"kind": "execution_end", "call_id": "a1", "agent_id": "planner", "timestamp": 2.0},
    ]
    src = tmp_path / "events.jsonl"
    out = tmp_path / "out.jsonl"
    src.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    replay_events_file(
        src,
        out,
        app_name="demo",
        converter_profile="observe_sdk",
        realtime=True,
    )
    names = [json.loads(line).get("name") for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "topology.node.started" in names
    assert "topology.node.completed" in names
    assert not any(str(n).endswith(".graph") for n in names)
