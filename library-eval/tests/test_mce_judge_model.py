#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.library.eval.mce.judge_model import (
    apply_eval_mce_model_defaults,
    resolve_judge_model,
    unique_application_model,
)


def test_default_is_defaults_model_when_nothing_set() -> None:
    resolved = resolve_judge_model()
    assert resolved.model is None
    assert resolved.source == "defaults.model"
    assert not resolved


def test_step_model_wins() -> None:
    resolved = resolve_judge_model(
        step_model="gpt-4o",
        evaluation_model="gpt-4o-mini",
        metadata={"model_name": "haiku"},
    )
    assert resolved.model == "gpt-4o"
    assert resolved.source == "eval_mce.config.model"


def test_evaluation_model_before_metadata() -> None:
    resolved = resolve_judge_model(
        evaluation_model="gpt-4o-mini",
        metadata={"model_name": "gpt-4o"},
    )
    assert (resolved.model, resolved.source) == (
        "gpt-4o-mini",
        "experiment.evaluation.model",
    )


def test_evaluation_config_model_alias() -> None:
    resolved = resolve_judge_model(evaluation_config={"model": "judge-x"})
    assert (resolved.model, resolved.source) == (
        "judge-x",
        "experiment.evaluation.config.model",
    )


def test_metadata_model_name_is_experiment_annotation() -> None:
    resolved = resolve_judge_model(metadata={"model_name": "gpt-4o"})
    assert (resolved.model, resolved.source) == (
        "gpt-4o",
        "experiment.metadata.model_name",
    )


def test_application_models_before_metadata() -> None:
    resolved = resolve_judge_model(
        application_model="gpt-4o",
        metadata={"model_name": "haiku"},
    )
    assert (resolved.model, resolved.source) == (
        "gpt-4o",
        "application.spec.models",
    )


def test_evaluation_model_before_application() -> None:
    resolved = resolve_judge_model(
        evaluation_model="gpt-4o-mini",
        application_model="gpt-4o",
    )
    assert resolved.model == "gpt-4o-mini"


def test_experiment_model_before_application() -> None:
    resolved = resolve_judge_model(
        experiment_model="gpt-4o",
        application_model="haiku",
    )
    assert (resolved.model, resolved.source) == ("gpt-4o", "experiment.model")


def test_experiment_models_judge_before_main() -> None:
    resolved = resolve_judge_model(
        experiment_judge_model="gpt-4o-mini",
        experiment_model="gpt-4o",
        application_model="haiku",
    )
    assert (resolved.model, resolved.source) == (
        "gpt-4o-mini",
        "experiment.models.judge",
    )


def test_evaluation_model_before_experiment_judge_slot() -> None:
    resolved = resolve_judge_model(
        evaluation_model="gpt-4o",
        experiment_judge_model="gpt-4o-mini",
    )
    assert resolved.model == "gpt-4o"


def test_any_is_not_a_pin() -> None:
    resolved = resolve_judge_model(
        experiment_model="any",
        application_model="gpt-4o",
    )
    assert (resolved.model, resolved.source) == ("gpt-4o", "application.spec.models")
    assert resolve_judge_model(experiment_model="any").model is None


def test_template_vars_eval_model() -> None:
    resolved = resolve_judge_model(template_vars={"eval_model": "tmpl-judge"})
    assert resolved.model == "tmpl-judge"
    assert resolved.source == "template_vars.eval_model"


def test_empty_strings_are_skipped() -> None:
    resolved = resolve_judge_model(
        step_model="  ",
        evaluation_model="",
        metadata={"model_name": "gpt-4o"},
    )
    assert resolved.model == "gpt-4o"


class _Step:
    def __init__(self, type: str, config: dict | None = None) -> None:
        self.type = type
        self.config = config if config is not None else {}


def test_apply_defaults_fills_omitted_eval_mce_only() -> None:
    from mas.library.eval.mce.judge_model import ResolvedJudgeModel

    steps = [
        _Step("eval_mce", {}),
        _Step("eval_mce", {"model": "already"}),
        _Step("extract_trajectories", {}),
    ]
    apply_eval_mce_model_defaults(steps, ResolvedJudgeModel("gpt-4o-mini", "experiment.evaluation.model"))
    assert steps[0].config["model"] == "gpt-4o-mini"
    assert steps[0].config["model_source"] == "experiment.evaluation.model"
    assert steps[1].config["model"] == "already"
    assert "model" not in steps[2].config


def test_step_judge_model_alias() -> None:
    resolved = resolve_judge_model(
        step_judge_model="haiku",
        evaluation_model="gpt-4o-mini",
    )
    assert (resolved.model, resolved.source) == (
        "haiku",
        "eval_mce.config.judge_model",
    )


def test_apply_defaults_skips_judge_model_only_step() -> None:
    from mas.library.eval.mce.judge_model import ResolvedJudgeModel

    steps = [_Step("eval_mce", {"judge_model": "already"})]
    apply_eval_mce_model_defaults(steps, ResolvedJudgeModel("gpt-4o-mini", "experiment.evaluation.model"))
    assert steps[0].config == {"judge_model": "already"}


def test_template_vars_after_evaluation_model() -> None:
    resolved = resolve_judge_model(
        evaluation_model="gpt-4o-mini",
        template_vars={"eval_model": "tmpl-judge"},
    )
    assert resolved.model == "gpt-4o-mini"


def test_template_vars_before_application_models() -> None:
    resolved = resolve_judge_model(
        application_model="gpt-4o",
        template_vars={"eval_model": "tmpl-judge"},
    )
    assert (resolved.model, resolved.source) == (
        "tmpl-judge",
        "template_vars.eval_model",
    )


def test_unique_application_model_from_mas_spec_models(tmp_path) -> None:
    mas = tmp_path / "mas.yaml"
    mas.write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata: {name: team}\n"
        "spec:\n  models:\n    - {id: main, model: gpt-4o}\n"
        "  agency:\n    agents: []\n",
        encoding="utf-8",
    )
    assert unique_application_model(mas) == ("gpt-4o", "application.spec.models")
    agent = tmp_path / "agent.yaml"
    agent.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: qa}\n"
        "spec:\n  description: x\n  models:\n    - {id: main, model: gpt-4o}\n"
        "    - {id: summarizer, model: gpt-4o-mini}\n",
        encoding="utf-8",
    )
    assert unique_application_model(agent) == ("gpt-4o", "application.spec.models")


def test_unique_application_model_from_mas_refs(tmp_path) -> None:
    (tmp_path / "agents").mkdir()
    for name in ("a", "b"):
        (tmp_path / "agents" / f"{name}.yaml").write_text(
            "apiVersion: mas/v1\nkind: Agent\n"
            f"metadata: {{name: {name}}}\n"
            "spec:\n  description: x\n  models:\n    - {model: gpt-4o}\n",
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
    assert unique_application_model(mas) == ("gpt-4o", "application.spec.models")


def test_unique_application_model_rejects_mixed_agents(tmp_path) -> None:
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
    assert unique_application_model(mas) == (None, "")
