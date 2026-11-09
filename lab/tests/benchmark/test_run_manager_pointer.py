#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from datetime import datetime
from pathlib import Path

from mas.lab.benchmark.metadata import BenchmarkMetadata, BenchmarkStatus
from mas.lab.benchmark.run_manager.manager import BenchmarkRunManager
from mas.lab.benchmark.run_manager.pointer import last_run_write_path, resolve_last_run_file


def _pin_xdg(fake_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))


def test_resolve_last_run_file_uses_xdg_state(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _pin_xdg(fake_home, monkeypatch)

    canonical = fake_home / ".local" / "state" / "mas"
    canonical.mkdir(parents=True)
    canonical_file = canonical / "last-run.json"
    canonical_file.write_text('{"run_dir": "/canonical"}', encoding="utf-8")

    assert resolve_last_run_file() == canonical_file
    assert last_run_write_path() == canonical_file


def test_resolve_last_run_file_missing_returns_none(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _pin_xdg(fake_home, monkeypatch)

    assert resolve_last_run_file() is None


def test_record_last_run_serializes_datetime_timestamp(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _pin_xdg(fake_home, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))

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
    ptr = last_run_write_path()
    text = ptr.read_text(encoding="utf-8")
    assert str(run_dir) in text
    assert str(yaml_path.resolve()) in text
    assert "2026-09-24" in text
