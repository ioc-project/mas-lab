#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.library.lab.steps.eval.mce import EvalMceStep


def _run_with_events(tmp_path: Path) -> Path:
    events = tmp_path / "topo" / "item1" / "r1" / "traces" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text('{"kind":"test"}\n', encoding="utf-8")
    return events


def _stub_scoring(monkeypatch) -> None:
    monkeypatch.setattr(
        "mas.library.eval.mce.runner.compute_session_metrics",
        lambda *args, **kwargs: {},
    )


@pytest.mark.asyncio
async def test_eval_mce_logs_and_records_judge_model(tmp_path, caplog, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    _stub_scoring(monkeypatch)
    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        lambda *args, **kwargs: kwargs.get("model_override") or "gpt-4o",
    )

    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "model": "gpt-4o-mini",
            "model_source": "experiment.evaluation.model",
            "validate": False,
        },
    )
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=None),
        template_vars={},
        scope_context=None,
    )
    with caplog.at_level("INFO"):
        out = await step.execute(ctx)

    assert out.metadata["judge_model"] == "gpt-4o-mini"
    assert out.metadata["judge_model_source"] == "experiment.evaluation.model"
    assert any("judge model=gpt-4o-mini" in rec.message for rec in caplog.records)
    assert any("source=experiment.evaluation.model" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_eval_mce_defaults_judge_when_model_omitted(tmp_path, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    _stub_scoring(monkeypatch)
    seen: dict[str, object] = {}

    def _install(model_override=None, **kwargs):
        seen["model_override"] = model_override
        seen.update(kwargs)
        return model_override or "gpt-4o"

    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        _install,
    )

    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "validate": False,
        },
    )
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=None),
        template_vars={"eval_model": "tmpl-judge"},
        scope_context=None,
    )
    out = await step.execute(ctx)
    assert seen.get("model_override") == "tmpl-judge"
    assert out.metadata["judge_model"] == "tmpl-judge"
    assert out.metadata["judge_model_source"] == "template_vars.eval_model"


@pytest.mark.asyncio
async def test_eval_mce_template_vars_beat_backfilled_application(tmp_path, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    _stub_scoring(monkeypatch)
    seen: dict[str, object] = {}

    def _install(model_override=None, **kwargs):
        seen["model_override"] = model_override
        seen.update(kwargs)
        return model_override or "gpt-4o"

    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        _install,
    )

    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "model": "gpt-4o",
            "model_source": "application.spec.models",
            "validate": False,
        },
    )
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=None),
        template_vars={"eval_model": "tmpl-judge"},
        scope_context=None,
    )
    out = await step.execute(ctx)
    assert seen.get("model_override") == "tmpl-judge"
    assert out.metadata["judge_model"] == "tmpl-judge"
    assert out.metadata["judge_model_source"] == "template_vars.eval_model"


@pytest.mark.asyncio
async def test_eval_mce_template_vars_beat_backfilled_metadata(tmp_path, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    _stub_scoring(monkeypatch)
    seen: dict[str, object] = {}

    def _install(model_override=None, **kwargs):
        seen["model_override"] = model_override
        seen.update(kwargs)
        return model_override or "gpt-4o"

    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        _install,
    )

    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "model": "gpt-4o",
            "model_source": "experiment.metadata.model_name",
            "validate": False,
        },
    )
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=None),
        template_vars={"eval_model": "tmpl-judge"},
        scope_context=None,
    )
    out = await step.execute(ctx)
    assert seen.get("model_override") == "tmpl-judge"
    assert out.metadata["judge_model"] == "tmpl-judge"
    assert out.metadata["judge_model_source"] == "template_vars.eval_model"


@pytest.mark.asyncio
async def test_eval_mce_backfilled_evaluation_model_beats_template_vars(tmp_path, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    _stub_scoring(monkeypatch)
    seen: dict[str, object] = {}

    def _install(model_override=None, **kwargs):
        seen["model_override"] = model_override
        seen.update(kwargs)
        return model_override or "gpt-4o"

    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        _install,
    )

    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "model": "gpt-4o-mini",
            "model_source": "experiment.evaluation.model",
            "validate": False,
        },
    )
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=None),
        template_vars={"eval_model": "tmpl-judge"},
        scope_context=None,
    )
    out = await step.execute(ctx)
    assert seen.get("model_override") == "gpt-4o-mini"
    assert out.metadata["judge_model_source"] == "experiment.evaluation.model"
