#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``experiment.overrides`` and ``--override .mas|.agent|.infra`` reach every run of a lab."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.benchmark.schedule.run_batch.load import _run_overrides, load_experiment

AGENT = "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: solo}\nspec:\n  models: [{model: gpt-4o-mini}]\n"
DATASET = (
    "apiVersion: lab/v1\nkind: Dataset\nmetadata: {name: d, version: '1.0'}\n"
    "spec:\n  items:\n  - id: q\n    inputs: {user: hi}\n"
)


def _lab(tmp_path: Path, overrides: str = "") -> Path:
    (tmp_path / "agent.yaml").write_text(AGENT, encoding="utf-8")
    (tmp_path / "data.yaml").write_text(DATASET, encoding="utf-8")
    (tmp_path / "experiment.yaml").write_text(
        "experiment:\n  name: o\n  application: {manifest: ./agent.yaml}\n"
        "  scenarios: [{id: base, overlays: {logic: [], control: [], infra: []}}]\n"
        f"  dataset: {{path: ./data.yaml}}\n  run: {{n_runs: 1}}\n{overrides}",
        encoding="utf-8",
    )
    return tmp_path / "experiment.yaml"


FILE_ITEM = ".infra.spec.models.defaults.llm=qwen2.5:7b-instruct"
CLI_ITEM = ".mas.agent[worker].spec.models[0].extra.cache.no-cache=true"


def test_the_file_overrides_come_first_then_the_command_line_ones(tmp_path):
    path = _lab(tmp_path, f"  overrides:\n    - {FILE_ITEM}\n")
    loaded = load_experiment(path, overrides=[CLI_ITEM, "experiment:experiment.run.n_runs=2"])
    assert loaded is not None
    assert loaded.run_overrides == (FILE_ITEM, CLI_ITEM)
    assert loaded.n_runs == 2  # an experiment: path still patches the file


def test_a_lab_with_no_overrides_has_none(tmp_path):
    loaded = load_experiment(_lab(tmp_path))
    assert loaded is not None and loaded.run_overrides == ()


@pytest.mark.parametrize("bad", ["experiment.name=x", "no-equals-sign"])
def test_a_path_that_is_not_a_run_override_is_refused_before_any_run(bad):
    with pytest.raises(ValueError):
        _run_overrides({"experiment": {"overrides": [bad]}}, [])


def test_overrides_must_be_a_list_of_strings():
    with pytest.raises(ValueError, match="list of PATH=VALUE"):
        _run_overrides({"experiment": {"overrides": "oops"}}, [])
