#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

import json
from pathlib import Path

import pytest
from mas.lab.benchmark.environment.clock import RunClock
from mas.lab.benchmark.environment.events import EventLog
from mas.lab.benchmark.environment.model import Limits, RunCoordinates
from mas.lab.schemas.paths import lab_schema_dir


def _log(tmp_path: Path, **kwargs) -> EventLog:
    return EventLog(
        path=tmp_path / "environment.jsonl",
        clock=RunClock(),
        run=RunCoordinates(experiment="e", scenario="s", item="i", run="r1"),
        limits=Limits(max_events_per_run=kwargs.get("max_events", 20)),
    )


def test_schema_and_seq_and_closed(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.emit("clock.started", source={"type": "environment", "id": "clock"})
    log.emit("action.done", source={"type": "trigger", "id": "t", "step": "http_call"}, payload={"status": 200})
    log.close()
    seqs = [r["seq"] for r in log.records()]
    assert seqs == list(range(1, len(seqs) + 1))
    assert log.records()[-1]["name"] == "environment.closed"
    import jsonschema

    schema = json.loads((lab_schema_dir() / "artefacts" / "environment.schema.json").read_text())
    validator = jsonschema.Draft7Validator(schema)
    for record in log.records():
        validator.validate(record)


def test_payload_keeps_extra_value_free_keys(tmp_path: Path) -> None:
    log = _log(tmp_path)
    rec = log.emit("action.done", payload={"status": 200, "count": 3})
    assert rec["payload"] == {"status": 200, "count": 3}


def test_value_free_rejects_secrets(tmp_path: Path) -> None:
    log = _log(tmp_path)
    with pytest.raises(ValueError, match="value-free"):
        log.emit("action.done", payload={"token": "sekrit"})
    with pytest.raises(ValueError, match="value-free"):
        log.emit("action.done", payload={"body": "http-body"})


def test_max_events_per_run(tmp_path: Path) -> None:
    log = _log(tmp_path, max_events=2)
    log.emit("clock.started")
    log.emit("clock.advanced", payload={"virtual_s": 1.0})
    # Past the cap the log stops growing; the run it observes is not failed.
    dropped = log.emit("clock.advanced", payload={"virtual_s": 2.0})
    assert dropped["outcome"] == "dropped"
    assert len(log) == 1  # one slot is kept for environment.closed
    closed = log.close()
    assert closed["payload"] == {"truncated": 1}
