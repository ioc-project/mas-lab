#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import inspect

import pytest
from mas.lab.benchmark.experiment import EvaluationSpec
from mas.lab.lab.config import experiment_base
from mas.lab.lab.config.experiment_base import MASRunBase


def test_evaluation_spec_positional_config_still_works() -> None:
    spec = EvaluationSpec("llm_judge", {"foo": 1})
    assert spec.method == "llm_judge"
    assert spec.config == {"foo": 1}
    assert spec.model is None


def test_evaluation_spec_reads_top_level_model() -> None:
    spec = EvaluationSpec.from_dict({"method": "llm_judge", "model": "gpt-4o-mini"})
    assert spec.method == "llm_judge"
    assert spec.model == "gpt-4o-mini"


def test_evaluation_spec_config_model_alias() -> None:
    spec = EvaluationSpec.from_dict(
        {"method": "llm_judge", "config": {"model": "haiku"}}
    )
    assert spec.model == "haiku"


def test_evaluation_spec_top_level_wins_over_config() -> None:
    spec = EvaluationSpec.from_dict(
        {"method": "llm_judge", "model": "gpt-4o-mini", "config": {"model": "ignored"}}
    )
    assert spec.model == "gpt-4o-mini"


def test_experiment_injects_eval_mce_model(tmp_path) -> None:
    data = {
        "name": "judge-override",
        "evaluation": {"method": "llm_judge", "model": "gpt-4o-mini"},
        "post": [
            {"type": "extract_trajectories"},
            {"type": "eval_mce", "depends_on": ["extract_trajectories"]},
            {"type": "eval_mce", "name": "strict", "config": {"model": "gpt-4o"}},
        ],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    steps = loaded["levels"]["experiment"].pipeline
    mce = [s for s in steps if s.type == "eval_mce"]
    assert len(mce) == 2
    inherited = next(s for s in mce if s.name != "strict")
    strict = next(s for s in mce if s.name == "strict")
    assert inherited.config["model"] == "gpt-4o-mini"
    assert inherited.config["model_source"] == "experiment.evaluation.model"
    assert strict.config["model"] == "gpt-4o"


def test_experiment_model_defaults_judge(tmp_path) -> None:
    data = {
        "name": "pinned",
        "model": "gpt-4o",
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o"
    assert step.config["model_source"] == "experiment.model"


def test_experiment_models_judge_defaults_eval_mce(tmp_path) -> None:
    data = {
        "name": "slots",
        "model": "gpt-4o",
        "models": {"judge": "gpt-4o-mini", "summarizer": "haiku"},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    assert loaded["model"] == "gpt-4o"
    assert loaded["models"]["judge"] == "gpt-4o-mini"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o-mini"
    assert step.config["model_source"] == "experiment.models.judge"


def test_experiment_models_main_wins_over_scalar(tmp_path) -> None:
    data = {
        "name": "both",
        "model": "gpt-4o",
        "models": {"main": "gpt-4o-mini"},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    assert loaded["model"] == "gpt-4o-mini"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o-mini"


def test_experiment_omitted_model_is_any(tmp_path) -> None:
    data = {
        "name": "unpinned",
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    assert loaded["model"] == "any"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert "model" not in step.config


def test_experiment_any_does_not_pin_judge(tmp_path) -> None:
    data = {
        "name": "unpinned",
        "model": "any",
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    step = loaded["levels"]["experiment"].pipeline[0]
    assert "model" not in step.config


def test_experiment_metadata_model_name_defaults_judge(tmp_path) -> None:
    data = {
        "name": "same-model",
        "metadata": {"model_name": "gpt-4o"},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o"
    assert step.config["model_source"] == "experiment.metadata.model_name"


def test_experiment_reads_application_agent_models(tmp_path) -> None:
    agent = tmp_path / "agent.yaml"
    agent.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: qa}\n"
        "spec:\n  description: x\n  models:\n    - {model: gpt-4o}\n",
        encoding="utf-8",
    )
    data = {
        "name": "from-agent",
        "application": {"manifest": str(agent)},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o"
    assert step.config["model_source"] == "application.spec.models"


def test_experiment_does_not_guess_when_agents_disagree(tmp_path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.yaml").write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: a}\n"
        "spec:\n  description: x\n  models:\n    - {model: gpt-4o}\n",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "b.yaml").write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: b}\n"
        "spec:\n  description: x\n  models:\n    - {model: gpt-4o-mini}\n",
        encoding="utf-8",
    )
    mas = tmp_path / "mas.yaml"
    mas.write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata: {name: team}\n"
        "spec:\n  agency:\n    agents:\n"
        "      - {id: a, ref: agents/a.yaml}\n"
        "      - {id: b, ref: agents/b.yaml}\n",
        encoding="utf-8",
    )
    data = {
        "name": "mixed",
        "application": {"manifest": str(mas)},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    step = loaded["levels"]["experiment"].pipeline[0]
    assert "model" not in step.config


def test_experiment_base_does_not_read_model_env() -> None:
    src = inspect.getsource(experiment_base)
    assert "os.environ" not in src
    assert "MAS_CTL_MODEL" not in src
    assert "MAS_LLM_MODEL" not in src


def test_model_override_also_sets_the_judge_default(tmp_path) -> None:
    data = {
        "name": "override",
        "model": "azure/gpt-4o-mini",
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data,
        tmp_path,
        yaml_path=tmp_path / "experiment.yaml",
        model_override="bedrock/claude-haiku",
    )
    assert loaded["model"] == "bedrock/claude-haiku"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "bedrock/claude-haiku"
    assert step.config["model_source"] == "experiment.model"


def test_model_override_wins_over_models_main(tmp_path) -> None:
    data = {
        "name": "override-slots",
        "model": "azure/gpt-4o-mini",
        "models": {"main": "azure/gpt-4o-mini"},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data,
        tmp_path,
        yaml_path=tmp_path / "experiment.yaml",
        model_override="bedrock/claude-haiku",
    )
    assert loaded["model"] == "bedrock/claude-haiku"
    assert loaded["models"]["main"] == "bedrock/claude-haiku"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "bedrock/claude-haiku"


def test_model_override_does_not_override_models_judge(tmp_path) -> None:
    data = {
        "name": "pinned-judge",
        "model": "azure/gpt-4o-mini",
        "models": {"judge": "gpt-4o-mini"},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data,
        tmp_path,
        yaml_path=tmp_path / "experiment.yaml",
        model_override="bedrock/claude-haiku",
    )
    assert loaded["model"] == "bedrock/claude-haiku"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o-mini"
    assert step.config["model_source"] == "experiment.models.judge"


def test_model_override_does_not_override_evaluation_model(tmp_path) -> None:
    data = {
        "name": "eval-pin",
        "model": "azure/gpt-4o-mini",
        "evaluation": {"method": "llm_judge", "model": "gpt-4o"},
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data,
        tmp_path,
        yaml_path=tmp_path / "experiment.yaml",
        model_override="bedrock/claude-haiku",
    )
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "gpt-4o"
    assert step.config["model_source"] == "experiment.evaluation.model"


def test_blank_model_override_does_not_override(tmp_path) -> None:
    data = {
        "name": "blank-override",
        "model": "azure/gpt-4o-mini",
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data,
        tmp_path,
        yaml_path=tmp_path / "experiment.yaml",
        model_override="  ",
    )
    assert loaded["model"] == "azure/gpt-4o-mini"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "azure/gpt-4o-mini"


def test_load_base_fields_ignores_model_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    data = {
        "name": "no-env",
        "model": "azure/gpt-4o-mini",
        "post": [{"type": "eval_mce"}],
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    assert loaded["model"] == "azure/gpt-4o-mini"
    step = loaded["levels"]["experiment"].pipeline[0]
    assert step.config["model"] == "azure/gpt-4o-mini"


def test_per_run_step_under_experiment_post_fails_fast(tmp_path) -> None:
    data = {
        "name": "misplaced",
        "post": [{"type": "metrics_to_dataframe", "per_run": True}],
    }
    with pytest.raises(ValueError, match="run: post:"):
        MASRunBase._load_base_fields(
            data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
        )


def test_per_run_step_under_run_post_still_loads(tmp_path) -> None:
    data = {
        "name": "placed",
        "run": {"post": [{"type": "metrics_to_dataframe", "per_run": True}]},
    }
    loaded = MASRunBase._load_base_fields(
        data, tmp_path, yaml_path=tmp_path / "experiment.yaml"
    )
    step = loaded["levels"]["run"].pipeline[0]
    assert step.per_run is True
