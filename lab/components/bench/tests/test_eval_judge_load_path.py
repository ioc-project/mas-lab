#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest
from mas.lab.benchmark.schedule.run_batch import run_mas_benchmark
from mas.lab.benchmark.schedule.run_batch.load import load_experiment
from mas.lab.lab.config import MASExperimentConfig
from mas.runtime.spec.source import load_yaml_file

_REPO = Path(__file__).resolve().parents[4]


def _write_experiment(tmp_path: Path, extra: str) -> Path:
    (tmp_path / "mas.yaml").write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata: {name: t}\n"
        "spec:\n  agency:\n    agents: []\n",
        encoding="utf-8",
    )
    (tmp_path / "overlays").mkdir(exist_ok=True)
    datasets = tmp_path / "datasets"
    datasets.mkdir(exist_ok=True)
    (datasets / "items.yaml").write_text(
        "spec:\n  items:\n    - id: q1\n      inputs: {user: hi}\n",
        encoding="utf-8",
    )
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "experiment:\n"
        "  name: load-path\n"
        "  model: azure/gpt-4o-mini\n"
        "  application:\n"
        "    manifest: ./mas.yaml\n"
        "    configs_dir: ./overlays\n"
        "  dataset:\n"
        "    path: ./datasets/items.yaml\n"
        "  scenarios:\n"
        "    - id: baseline\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_per_run_under_experiment_post_fails_on_dry_run(
    tmp_path, caplog
) -> None:
    path = _write_experiment(
        tmp_path,
        "  post:\n"
        "    - type: metrics_to_dataframe\n"
        "      per_run: true\n",
    )
    with caplog.at_level(logging.ERROR):
        ok = await run_mas_benchmark(path, dry_run=True)
    assert ok is False
    assert "run: post:" in caplog.text


def test_load_experiment_env_override_sets_judge(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    path = _write_experiment(
        tmp_path,
        "  post:\n"
        "    - type: eval_mce\n",
    )
    loaded = load_experiment(path)
    assert loaded is not None
    step = loaded.exp.levels["experiment"].pipeline[0]
    assert loaded.exp.model == "bedrock/claude-haiku"
    assert step.config["model"] == "bedrock/claude-haiku"


def test_from_yaml_without_override_ignores_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    path = _write_experiment(
        tmp_path,
        "  post:\n"
        "    - type: eval_mce\n",
    )
    exp = MASExperimentConfig.from_yaml(path)
    step = exp.levels["experiment"].pipeline[0]
    assert exp.model == "azure/gpt-4o-mini"
    assert step.config["model"] == "azure/gpt-4o-mini"


def test_from_yaml_explicit_override_beats_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    path = _write_experiment(
        tmp_path,
        "  post:\n"
        "    - type: eval_mce\n",
    )
    exp = MASExperimentConfig.from_yaml(
        path, model_override="azure/gpt-4o"
    )
    step = exp.levels["experiment"].pipeline[0]
    assert exp.model == "azure/gpt-4o"
    assert step.config["model"] == "azure/gpt-4o"


def test_from_data_without_override_ignores_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    path = _write_experiment(
        tmp_path,
        "  post:\n"
        "    - type: eval_mce\n",
    )
    exp = MASExperimentConfig.from_data(load_yaml_file(path), path)
    step = exp.levels["experiment"].pipeline[0]
    assert exp.model == "azure/gpt-4o-mini"
    assert step.config["model"] == "azure/gpt-4o-mini"


def test_from_yaml_does_not_read_env() -> None:
    src = inspect.getsource(MASExperimentConfig.from_yaml)
    assert "env_model_override" not in src


def test_pipeline_step_cmd_passes_env_model_override() -> None:
    src = (_REPO / "lab/src/mas/lab/cli/commands/run.py").read_text(
        encoding="utf-8"
    )
    assert "model_override=env_model_override()" in src


def test_read_only_loaders_do_not_call_env_model_override() -> None:
    files = (
        "lab/components/bench/src/mas/lab/benchmark/cli/tree.py",
        "lab/components/bench/src/mas/lab/benchmark/cli/clean.py",
        "lab/components/bench/src/mas/lab/benchmark/cli/analyze.py",
        "lab/components/bench/src/mas/lab/benchmark/cli/artifacts.py",
        "lab/src/mas/lab/cli/commands/benchmark/follow.py",
        "lab/components/bench/src/mas/lab/benchmark/engine.py",
    )
    for rel in files:
        src = (_REPO / rel).read_text(encoding="utf-8")
        assert "env_model_override" not in src, rel
