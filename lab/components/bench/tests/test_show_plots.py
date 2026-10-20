#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace

from mas.lab.benchmark.cli.show import _show_plots


def _write(path, content: bytes | str = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


def test_show_plots_lists_type_plot_artifacts_in_lab_tree(tmp_path, capsys):
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n"
        "  artifacts:\n"
        "    plot: plot\n"
        "  run:\n"
        "    artifacts:\n"
        "      trajectory-native:\n"
        "        type: plot\n"
        "        path: '{run_dir}/trajectory-native.html'\n"
        "      metrics: metrics\n",
        encoding="utf-8",
    )
    _write(tmp_path / "plot.png")
    _write(tmp_path / "data.csv", "a,b\n")
    _write(tmp_path / "baseline" / "plot.png")
    _write(tmp_path / "baseline" / "item1" / "plot.png")
    run = tmp_path / "baseline" / "item1" / "r1"
    _write(run / "plot.png")
    _write(run / "metrics.json", "{}")
    _write(run / "trajectory-native.html", "<html/>")
    _write(tmp_path / "results" / "fig_trajectory.svg", "<svg/>")
    _write(tmp_path / "plots" / "legacy.png")

    rc = _show_plots(
        SimpleNamespace(experiment_yaml_path=str(yaml_path)),
        tmp_path,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "plot.png" in out
    assert "trajectory-native.html" in out
    assert "[plot]" in out
    assert "baseline/plot.png" not in out
    assert "baseline/item1/plot.png" not in out
    assert "baseline/item1/r1/plot.png" not in out
    assert "data.csv" not in out
    assert "metrics.json" not in out
    assert "fig_trajectory.svg" not in out
    assert "legacy.png" not in out


def test_show_plots_errors_when_no_plot_artifacts(tmp_path, capsys):
    _write(tmp_path / "data.csv", "a,b\n")
    _write(tmp_path / "results" / "fig.svg", "<svg/>")
    rc = _show_plots(SimpleNamespace(), tmp_path)
    assert rc == 1
    out = capsys.readouterr().out
    assert "No plot artifacts found" in out
    assert "fig.svg" not in out


def test_show_plots_ignores_undeclared_pipeline_output_files(tmp_path, capsys):
    yaml_path = tmp_path / "experiment.yaml"
    yaml_path.write_text(
        "experiment:\n"
        "  artifacts:\n"
        "    fig-fork: plot\n"
        "    trace_stats: dataframe\n"
        "  application:\n"
        "    post:\n"
        "      - name: figure-fork\n"
        "        type: plotnine\n"
        "        config:\n"
        "          output: 'fig-fork.png'\n"
        "      - name: extract-stats\n"
        "        type: extract_trace_stats\n"
        "        config:\n"
        "          output: 'trace_stats.csv'\n",
        encoding="utf-8",
    )
    _write(tmp_path / "fig-fork.png")
    _write(tmp_path / "trace_stats.csv", "a,b\n")
    _write(tmp_path / "orphan.png")
    _write(tmp_path / "results" / "fig-fork.png")

    rc = _show_plots(
        SimpleNamespace(experiment_yaml_path=str(yaml_path)),
        tmp_path,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Plot artifacts (1):" in out
    assert "fig-fork" in out
    assert "fig-fork.png" in out
    assert "trace_stats.csv" not in out
    assert "orphan.png" not in out
    assert "results/fig-fork.png" not in out


def test_show_plots_ignores_unreadable_experiment_yaml(tmp_path, capsys):
    _write(tmp_path / "plot.png")
    rc = _show_plots(
        SimpleNamespace(experiment_yaml_path=str(tmp_path / "missing.yaml")),
        tmp_path,
    )
    assert rc == 1
    assert "plot.png" not in capsys.readouterr().out
