#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``in:`` / ``out:`` are dataflow edges: a reader runs after the writers.

They used to be metadata for fan-in only; ordering came from level order plus
explicit ``depends_on``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from mas.lab.benchmark.schedule.pipeline import materialize_step_dicts
from mas.lab.lab.config import PipelineStepSpec


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    (tmp_path / "s1" / "item1" / "r1").mkdir(parents=True)
    return tmp_path


def _spec(name, scope, *, inp=(), out=(), phase="post", deps=()):
    return PipelineStepSpec(
        type="shell",
        name=name,
        scope=scope,
        phase=phase,
        inputs=list(inp),
        outputs=list(out),
        depends_on=list(deps),
    )


def _deps(specs, out_dir, phase="post") -> dict[str, list[str]]:
    dicts = materialize_step_dicts(
        specs,
        phase=phase,
        scenario_ids=["s1"],
        infra_name=None,
        step_overrides={},
        template_vars={"output_dir": str(out_dir)},
    )
    return {d["name"]: list(d["depends_on"]) for d in dicts}


def test_reader_follows_writer_at_the_same_level(out_dir):
    deps = _deps([_spec("a", "run", out=["m"]), _spec("b", "run", inp=["m"])], out_dir)
    assert deps["b-s1-item1-r1"] == ["a-s1-item1-r1"]


def test_higher_level_reader_follows_the_lower_level_writers(out_dir):
    deps = _deps(
        [_spec("score", "run", inp=["trace"], out=["metrics"]), _spec("agg", "scenario", inp=["metrics"])],
        out_dir,
    )
    assert deps["agg-s1"] == ["score-s1-item1-r1"]


def test_nearest_producer_wins(out_dir):
    deps = _deps(
        [
            _spec("per-run", "run", out=["df"]),
            _spec("gather", "scenario", inp=["df"], out=["df"]),
            _spec("plot", "scenario", inp=["df"]),
        ],
        out_dir,
    )
    assert deps["plot-s1"] == ["gather-s1"]  # not the run-level writer
    assert deps["gather-s1"] == ["per-run-s1-item1-r1"]


def test_explicit_depends_on_is_kept_and_not_duplicated(out_dir):
    deps = _deps(
        [_spec("a", "run", out=["m"]), _spec("b", "run", inp=["m"], deps=["a"])],
        out_dir,
    )
    assert deps["b-s1-item1-r1"] == ["a-s1-item1-r1"]


def test_pre_writers_do_not_gate_post_readers(out_dir):
    specs = [_spec("gen", "experiment", out=["ds"], phase="pre"), _spec("use", "experiment", inp=["ds"])]
    assert _deps(specs, out_dir, phase="post") == {"use": []}


def test_engine_artifacts_do_not_warn_but_missing_writers_do(out_dir, caplog):
    with caplog.at_level(logging.WARNING, logger="mas.lab.benchmark.schedule.pipeline"):
        _deps([_spec("x", "run", inp=["trace"]), _spec("y", "run", inp=["nobody-writes-this"])], out_dir)
    text = caplog.text
    assert "nobody-writes-this" in text
    assert "'trace'" not in text
