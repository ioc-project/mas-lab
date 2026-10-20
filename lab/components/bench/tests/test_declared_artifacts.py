#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from mas.lab.benchmark.cli.artifacts import (
    artifact_list_command,
    format_artifact_list,
)
from mas.lab.benchmark.cli.declared import (
    artifact_for_file,
    catalog_from_config,
    catalog_from_yaml,
    locate_artifacts,
    plan_artifacts,
)
from mas.lab.lab.config.pipeline import ArtifactSpec


def _write(path: Path, content: bytes | str = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def test_relative_path_uses_name_and_type_format() -> None:
    spec = ArtifactSpec(name="fig-fork", type="plot")
    assert spec.relative_path() == Path("fig-fork.png")


def test_relative_path_keeps_subdir_from_template() -> None:
    spec = ArtifactSpec(
        name="trace",
        type="trace",
        path="{run_dir}/traces/events.jsonl",
    )
    assert spec.relative_path() == Path("traces/events.jsonl")


def test_catalog_from_yaml_reads_level_artifacts(tmp_path: Path) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n"
        "  artifacts:\n"
        "    fig-fork: plot\n"
        "    trace_stats: dataframe\n"
        "  application:\n"
        "    artifacts:\n"
        "      figure-02:\n"
        "        type: plot\n"
        "        path: 'figure-02.png'\n"
        "  run:\n"
        "    artifacts:\n"
        "      metrics: metrics\n",
        encoding="utf-8",
    )
    catalog = catalog_from_yaml(yaml_path)
    by_name = {spec.name: (level, spec) for level, spec in catalog}
    assert by_name["fig-fork"][0] == "experiment"
    assert by_name["fig-fork"][1].type == "plot"
    assert by_name["fig-fork"][1].relative_path() == Path("fig-fork.png")
    assert by_name["trace_stats"][1].type == "dataframe"
    assert by_name["figure-02"][0] == "application"
    assert by_name["figure-02"][1].relative_path() == Path("figure-02.png")
    assert by_name["metrics"][0] == "run"
    assert by_name["metrics"][1].relative_path() == Path("metrics.json")


def test_locate_and_identify_from_declared_artifacts(tmp_path: Path) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n"
        "  artifacts:\n"
        "    fig-fork: plot\n"
        "    trace_stats: dataframe\n"
        "  run:\n"
        "    artifacts:\n"
        "      metrics: metrics\n"
        "      trajectory-native:\n"
        "        type: plot\n"
        "        path: '{run_dir}/trajectory-native.html'\n",
        encoding="utf-8",
    )
    _write(tmp_path / "fig-fork.png")
    _write(tmp_path / "trace_stats.csv", "a,b\n")
    _write(tmp_path / "orphan.png")
    _write(tmp_path / "results" / "fig-fork.png")
    run = tmp_path / "baseline" / "item1" / "r1"
    _write(run / "metrics.json", "{}")
    _write(run / "trajectory-native.html", "<html/>")
    _write(run / "kg.json", "{}")

    catalog = catalog_from_yaml(yaml_path)
    found = locate_artifacts(tmp_path, catalog)
    names = {(a.name, a.type, a.level) for a in found}
    assert names == {
        ("fig-fork", "plot", "experiment"),
        ("trace_stats", "dataframe", "experiment"),
        ("metrics", "metrics", "run"),
        ("trajectory-native", "plot", "run"),
    }

    plots = locate_artifacts(tmp_path, catalog, type_filter="plot")
    assert {a.name for a in plots} == {"fig-fork", "trajectory-native"}

    hit = artifact_for_file(tmp_path / "fig-fork.png", catalog, tmp_path)
    assert hit is not None
    assert hit.name == "fig-fork"
    assert hit.type == "plot"
    assert hit.level == "experiment"

    run_hit = artifact_for_file(run / "metrics.json", catalog, tmp_path)
    assert run_hit is not None
    assert run_hit.name == "metrics"
    assert run_hit.type == "metrics"
    assert run_hit.level == "run"

    assert artifact_for_file(tmp_path / "orphan.png", catalog, tmp_path) is None
    assert artifact_for_file(tmp_path / "results" / "fig-fork.png", catalog, tmp_path) is None
    assert artifact_for_file(run / "kg.json", catalog, tmp_path) is None


def test_catalog_from_unreadable_yaml_is_empty(tmp_path: Path) -> None:
    assert catalog_from_yaml(tmp_path / "missing.yaml") == []
    assert catalog_from_yaml(None) == []


def test_catalog_from_config_reads_declared_artifacts() -> None:
    spec = ArtifactSpec(name="fig-fork", type="plot")
    exp = SimpleNamespace(declared_artifacts=lambda: [("application", spec)])
    assert catalog_from_config(exp) == [("application", spec)]
    assert catalog_from_config(SimpleNamespace()) == []


def test_list_marks_generated_and_missing(tmp_path: Path) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n"
        "  artifacts:\n"
        "    fig-fork: plot\n"
        "    not-written: plot\n"
        "  run:\n"
        "    artifacts:\n"
        "      metrics: metrics\n",
        encoding="utf-8",
    )
    _write(tmp_path / "fig-fork.png")
    _write(tmp_path / "orphan.png")
    _write(tmp_path / "baseline" / "item1" / "r1" / "metrics.json", "{}")
    catalog = catalog_from_yaml(yaml_path)
    slots = plan_artifacts(tmp_path, catalog)
    by_key = {(s.name, s.location or "."): s for s in slots}
    assert by_key[("fig-fork", ".")].generated is True
    assert by_key[("not-written", ".")].generated is False
    assert by_key[("metrics", "baseline/item1/r1")].generated is True
    assert artifact_for_file(tmp_path / "orphan.png", catalog, tmp_path) is None

    lines = "\n".join(format_artifact_list(slots))
    assert "generated" in lines
    assert "missing" in lines
    assert "fig-fork" in lines
    assert "not-written" in lines
    assert "baseline/item1/r1" in lines
    assert "orphan" not in lines

    app_only = "\n".join(format_artifact_list(slots, level_filter="application"))
    assert "fig-fork" in app_only
    assert "metrics.json" not in app_only


def test_list_command_uses_output_dir(tmp_path: Path, capsys, monkeypatch) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n"
        "  artifacts:\n"
        "    fig-fork: plot\n"
        "    not-written: plot\n",
        encoding="utf-8",
    )
    _write(tmp_path / "fig-fork.png")
    monkeypatch.setattr(
        "mas.lab.benchmark.cli.artifacts._output_dir_for",
        lambda _yaml: tmp_path,
    )
    rc = artifact_list_command(SimpleNamespace(
        target=str(yaml_path),
        artifact_type=None,
        level=None,
        verbose=False,
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fig-fork" in out
    assert "not-written" in out
    assert "generated" in out
    assert "missing" in out
    assert "orphan" not in out


def test_list_yaml_uses_last_run_dir(tmp_path: Path, capsys, monkeypatch) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n  artifacts:\n    fig-fork: plot\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "paper-run"
    _write(run_dir / "fig-fork.png")
    monkeypatch.setattr(
        "mas.lab.benchmark.cli.artifacts._last_run",
        lambda: (SimpleNamespace(experiment_yaml_path=str(yaml_path)), run_dir),
    )
    rc = artifact_list_command(SimpleNamespace(
        target=str(yaml_path),
        artifact_type=None,
        level=None,
        verbose=False,
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fig-fork" in out
    assert "generated" in out


def test_list_command_rejects_directory(tmp_path: Path, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="mas.lab.benchmark.cli.artifacts"):
        rc = artifact_list_command(SimpleNamespace(
            target=str(tmp_path),
            artifact_type=None,
            level=None,
            verbose=False,
        ))
    assert rc == 1
    assert "not a directory" in caplog.text


def test_list_command_uses_last_run(tmp_path: Path, capsys, monkeypatch) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n  artifacts:\n    fig-fork: plot\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "paper-run"
    _write(run_dir / "fig-fork.png")
    monkeypatch.setattr(
        "mas.lab.benchmark.cli.artifacts._last_run",
        lambda: (SimpleNamespace(experiment_yaml_path=str(yaml_path)), run_dir),
    )
    rc = artifact_list_command(SimpleNamespace(
        target="last",
        artifact_type=None,
        level=None,
        verbose=False,
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fig-fork" in out
    assert "generated" in out


def test_list_command_uses_benchmark_id(tmp_path: Path, capsys, monkeypatch) -> None:
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n  artifacts:\n    fig-fork: plot\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "paper-run"
    _write(run_dir / "fig-fork.png")

    class _Mgr:
        def get_run(self, bid: str):
            if bid != "abcd1234":
                return None
            return SimpleNamespace(experiment_yaml_path=str(yaml_path)), run_dir

    monkeypatch.setattr("mas.lab.benchmark.cli.artifacts._run_manager", _Mgr)
    rc = artifact_list_command(SimpleNamespace(
        target="abcd1234",
        artifact_type=None,
        level=None,
        verbose=False,
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fig-fork" in out
    assert "generated" in out


def test_show_artifact_by_id_uses_last_run(tmp_path: Path, capsys, monkeypatch) -> None:
    import hashlib

    from mas.lab.benchmark.cli.show import show_artifact_by_id_command

    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n  artifacts:\n    fig-fork: plot\n",
        encoding="utf-8",
    )
    png = tmp_path / "fig-fork.png"
    _write(png, b"plot-bytes")
    art_id = hashlib.sha256(png.read_bytes()).hexdigest()[:8]
    metadata = SimpleNamespace(experiment_yaml_path=str(yaml_path))

    class _Mgr:
        def get_last_run(self):
            return metadata, tmp_path

        def list_runs(self):
            return []

    monkeypatch.setattr(
        "mas.lab.benchmark.cli.show.BenchmarkRunManager",
        lambda: _Mgr(),
    )
    rc = show_artifact_by_id_command(SimpleNamespace(artifact_id=art_id))
    assert rc == 0
    out = capsys.readouterr().out
    assert art_id in out
    assert "fig-fork" in out
    assert "plot" in out
