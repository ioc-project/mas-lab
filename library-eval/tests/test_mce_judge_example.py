#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.lab.lab.config.experiment_base import MASRunBase
from mas.library.eval.mce.judge_model import resolve_judge_model

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mce" / "judge-override"


def test_sample_files_exist() -> None:
    assert (SAMPLE / "experiment.yaml").is_file()
    assert (SAMPLE / "mas.yaml").is_file()
    assert (SAMPLE / "README.md").is_file()
    readme = (SAMPLE / "README.md").read_text(encoding="utf-8")
    assert "library-eval/examples/mce/judge-override" in readme
    assert "library-samples/apps" not in readme
    assert "evaluation.model" in readme or "models.judge" in readme
    assert "application.spec.models" in readme
    assert "config.yaml" in readme or "experiment.model" in readme or "experiment.models" in readme


def test_sample_experiment_validates() -> None:
    pytest.importorskip("jsonschema")
    from mas.ctl.validate import validate_file

    result = validate_file(SAMPLE / "experiment.yaml", kind="experiment", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_sample_injects_evaluation_model_and_keeps_step_override() -> None:
    data = yaml.safe_load((SAMPLE / "experiment.yaml").read_text(encoding="utf-8"))
    exp = data["experiment"]
    loaded = MASRunBase._load_base_fields(exp, SAMPLE, yaml_path=SAMPLE / "experiment.yaml")
    steps = [s for s in loaded["levels"]["experiment"].pipeline if s.type == "eval_mce"]
    assert len(steps) == 2
    inherited = next(s for s in steps if s.name != "eval_mce_strict")
    strict = next(s for s in steps if s.name == "eval_mce_strict")
    assert inherited.config["model"] == "gpt-4o-mini"
    assert inherited.config["model_source"] == "experiment.models.judge"
    assert strict.config["model"] == "gpt-4o"


def test_step_model_still_wins_over_evaluation() -> None:
    resolved = resolve_judge_model(
        step_model="gpt-4o",
        evaluation_model="gpt-4o-mini",
        metadata={"model_name": "haiku"},
    )
    assert (resolved.model, resolved.source) == ("gpt-4o", "eval_mce.config.model")
