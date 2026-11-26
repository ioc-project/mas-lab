#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Offline companion lab: successive-halving of a factorial space."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from mas.lab.benchmark.control import next_runs
from mas.lab.benchmark.execution.roles import PlannedRun
from mas.lab.benchmark.pipeline.core import Pipeline

ROOT = Path(__file__).resolve().parents[4]
HALVING = ROOT / "labs" / "factorial-halving.lab"


def test_factorial_halving_pipeline_parses_and_keeps_top_half() -> None:
    pipe = Pipeline.from_yaml(HALVING / "pipeline.yaml")
    assert pipe.steps[0].type == "next_runs"
    scores = json.loads((HALVING / "fixtures" / "round1-scores.json").read_text(encoding="utf-8"))
    runs = [PlannedRun(sid, {"id": 1}, 0) for sid in scores]
    kept = next_runs(runs=runs, scores=scores, strategy="successive_halving", keep_fraction=0.5)
    assert [c.scenario_id for c in kept] == ["small-short", "small-long"]


def test_factorial_halving_experiment_loads_without_clean_params() -> None:
    text = (HALVING / "experiment.yaml").read_text(encoding="utf-8")
    assert "clean:" not in text
    data = yaml.safe_load(text)
    executor = data["experiment"]["executor"]
    assert "clean" not in (executor.get("params") or {})
    assert (HALVING / "agent.yaml").is_file()
    assert (HALVING / "dataset.yaml").is_file()
    assert not (ROOT / "labs" / "score-sweep.lab").exists()
    assert not (ROOT / "docs" / "blog" / "posts" / "2026-11-25-score-sweep").exists()
    assert not (ROOT / "docs" / "blog" / "posts" / "2026-11-25-factorial-halving").exists()
