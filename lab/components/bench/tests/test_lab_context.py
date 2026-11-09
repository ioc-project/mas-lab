#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas.lab.lab.config.lab_context -- discover_lab_context() must find
a lab's own lab-config.yaml regardless of how many directories the experiment
YAML is nested below the lab root. It used to check only the experiment's own
directory, so libraries:/plugins: declarations in a root-level lab-config.yaml
were silently never applied for any experiment nested more than one level
deep (the common case: <lab>.lab/experiments/<name>/experiment.yaml).
"""

from __future__ import annotations

import sys

from mas.lab.lab.config.lab_context import (
    _discover_lab_name,
    discover_lab_context,
    inject_lab_libraries,
)


def _write_lab_config(lab_dir, *, name="my-lab", libraries=None, plugins=None) -> None:
    lines = ["lab:", f'  name: "{name}"']
    if libraries:
        lines.append("  libraries:")
        lines += [f"    - {lib}" for lib in libraries]
    if plugins:
        lines.append("  plugins:")
        for p in plugins:
            lines.append(f"    - path: {p['path']}")
            lines.append(f"      module: {p['module']}")
    (lab_dir / "lab-config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_discover_lab_context_finds_lab_config_one_level_up(tmp_path) -> None:
    lab_dir = tmp_path / "some.lab"
    exp_dir = lab_dir / "experiments" / "01-smoke"
    exp_dir.mkdir(parents=True)
    _write_lab_config(lab_dir, name="some-lab")

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_dir == lab_dir.resolve()
    assert ctx.lab_yaml == lab_dir.resolve() / "lab-config.yaml"
    assert ctx.lab_name == "some-lab"


def test_discover_lab_context_finds_lab_config_several_levels_up(tmp_path) -> None:
    """The actual regression case: experiments/<hackathon>/<pattern>/pipelines/
    is 4 levels below the lab root, not 1."""
    lab_dir = tmp_path / "trip-planner.lab"
    exp_dir = lab_dir / "experiments" / "hackathon-experiments" / "centralized-moderator"
    pipelines_dir = exp_dir / "pipelines"
    pipelines_dir.mkdir(parents=True)
    _write_lab_config(lab_dir, name="trip-planner", libraries=["lib/"])

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_dir == lab_dir.resolve()
    assert ctx.lab_name == "trip-planner"
    assert ctx.libraries == ["lib/"]


def test_discover_lab_context_does_not_cross_a_lab_boundary(tmp_path) -> None:
    """An experiment inside inner.lab must never pick up outer.lab's config."""
    outer_lab = tmp_path / "outer.lab"
    inner_lab = outer_lab / "nested" / "inner.lab"
    exp_dir = inner_lab / "experiments" / "01-smoke"
    exp_dir.mkdir(parents=True)
    _write_lab_config(outer_lab, name="outer-lab")
    # inner.lab deliberately has no lab-config.yaml of its own.

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_yaml is None
    assert ctx.lab_name == "inner"  # falls back to the .lab dir name, not "outer-lab"


def test_discover_lab_context_falls_back_to_experiment_dir_when_no_lab_config(
    tmp_path,
) -> None:
    exp_dir = tmp_path / "experiments" / "01-smoke"
    exp_dir.mkdir(parents=True)

    ctx = discover_lab_context(exp_dir / "experiment.yaml")

    assert ctx.lab_dir == exp_dir.resolve()
    assert ctx.lab_yaml is None


def test_discover_lab_name_matches_discover_lab_context(tmp_path) -> None:
    lab_dir = tmp_path / "cognitive-mas" / "trip-planner.lab"
    exp_dir = lab_dir / "experiments" / "top1-smoke"
    exp_dir.mkdir(parents=True)
    _write_lab_config(lab_dir, name="trip-planner")

    assert _discover_lab_name(exp_dir / "experiment.yaml") == "trip-planner"


def test_inject_lab_libraries_puts_the_lab_root_on_sys_path_not_the_experiment_dir(tmp_path, monkeypatch) -> None:
    lab_dir = tmp_path / "some.lab"
    exp_dir = lab_dir / "experiments" / "deeply" / "nested" / "example"
    exp_dir.mkdir(parents=True)
    _write_lab_config(lab_dir)

    monkeypatch.setattr(sys, "path", list(sys.path))
    ctx = discover_lab_context(exp_dir / "experiment.yaml")
    inject_lab_libraries(ctx)

    assert str(lab_dir.resolve()) in sys.path
    assert str(exp_dir.resolve()) not in sys.path


def test_inject_lab_libraries_resolves_libraries_relative_to_the_lab_root(tmp_path, monkeypatch) -> None:
    lab_dir = tmp_path / "some.lab"
    exp_dir = lab_dir / "experiments" / "a" / "b" / "c"
    exp_dir.mkdir(parents=True)
    lib_dir = lab_dir / "lib"
    lib_dir.mkdir()
    _write_lab_config(lab_dir, libraries=["lib/"])

    monkeypatch.setattr(sys, "path", list(sys.path))
    ctx = discover_lab_context(exp_dir / "experiment.yaml")
    inject_lab_libraries(ctx)

    assert str(lib_dir.resolve()) in sys.path
