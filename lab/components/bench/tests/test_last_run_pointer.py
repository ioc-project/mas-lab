#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mas.lab.benchmark.metadata import BenchmarkMetadata, BenchmarkStatus
from mas.lab.benchmark.run_manager.manager import BenchmarkRunManager
from mas.lab.benchmark.run_manager.pointer import last_run_write_path


def _pin_xdg(fake_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))


def test_record_last_run_serializes_datetime_and_yaml_override(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _pin_xdg(fake_home, monkeypatch)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text("experiment:\n  name: demo\n", encoding="utf-8")
    metadata = BenchmarkMetadata(
        benchmark_id="id-1",
        timestamp=datetime(2026, 9, 24, 1, 0, 0),  # type: ignore[arg-type]
        experiment_name="demo",
        experiment_description="",
        experiment_yaml_path=str(tmp_path / "other.yaml"),
        status=BenchmarkStatus.COMPLETED,
        total_scenarios=1,
    )
    BenchmarkRunManager(benchmarks_root=tmp_path / "labs").record_last_run(
        metadata,
        run_dir,
        experiment_yaml=yaml_path,
    )
    text = last_run_write_path().read_text(encoding="utf-8")
    assert str(run_dir) in text
    assert str(yaml_path.resolve()) in text
    assert "2026-09-24" in text


def test_metadata_from_yaml_coerces_datetime_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "metadata.yaml"
    path.write_text(
        "benchmark_id: id-1\n"
        "timestamp: 2026-09-24T01:00:00\n"
        "experiment_name: demo\n"
        "experiment_description: ''\n"
        "experiment_yaml_path: /tmp/exp.yaml\n"
        "status: completed\n"
        "total_scenarios: 1\n",
        encoding="utf-8",
    )
    meta = BenchmarkMetadata.from_yaml(path)
    assert isinstance(meta.timestamp, str)
    assert meta.timestamp.startswith("2026-09-24")
