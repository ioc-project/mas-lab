#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.library.eval.evaluator import DEFAULT_METRIC_PROVIDER, get_provider
from mas.library.eval.metrics.plugins import reset_metrics_for_tests
from mas.library.eval.metrics.toy import ToyEchoMetric
from mas.library.lab.steps.eval.mce import EvalMceStep


def _run_with_events(tmp_path: Path) -> Path:
    events = tmp_path / "topo" / "item1" / "r1" / "traces" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text('{"kind":"user_message","content":"hi"}\n', encoding="utf-8")
    return events


def _ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=None),
        template_vars={},
        scope_context=None,
    )


@pytest.fixture(autouse=True)
def _clean_hosted_metrics():
    reset_metrics_for_tests()
    yield
    reset_metrics_for_tests()


@pytest.mark.asyncio
async def test_eval_mce_routes_mixed_ids(tmp_path, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    get_provider(DEFAULT_METRIC_PROVIDER).register_metric(ToyEchoMetric())
    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        lambda *args, **kwargs: kwargs.get("model_override") or "gpt-4o",
    )

    def _stock(trace_path, names, **kwargs):
        return {name: {"value": 0.5, "reasoning": "stub", "error": None} for name in names} | {
            "__run_quality__": {"warnings": [], "errors": [], "status": "ok"}
        }

    monkeypatch.setattr(
        "mas.library.eval.mce.runner.compute_session_metrics",
        _stock,
    )

    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "metrics": ["groundedness", "toy_echo"],
            "scenario": "baseline",
            "test": "item1",
            "validate": True,
            "model": "gpt-4o-mini",
        },
    )
    out = await step.execute(_ctx(tmp_path))
    metrics_file = Path(out.metadata["output"])
    doc = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert doc["session"]["groundedness"]["value"] == 0.5
    assert doc["session"]["toy_echo"]["value"] == 1.0
    assert doc["session"]["toy_echo"]["details"]["bytes"] > 0
    assert doc["run_quality"]["status"] == "ok"

    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "docs" / "schemas" / "lab" / "artefacts" / "metrics.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(doc, schema)


@pytest.mark.asyncio
async def test_eval_mce_unknown_id_errors(tmp_path, monkeypatch) -> None:
    events = _run_with_events(tmp_path)
    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        lambda *args, **kwargs: "gpt-4o",
    )
    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "metrics": ["not_registered"],
            "validate": False,
        },
    )
    out = await step.execute(_ctx(tmp_path))
    assert out.data["errors"] == 1
    doc = json.loads(Path(out.metadata["output"]).read_text(encoding="utf-8"))
    assert doc["run_quality"]["status"] == "error"
    assert any("not_registered" in err for err in doc["run_quality"]["errors"])


@pytest.mark.asyncio
async def test_eval_mce_isolates_registry_failure(tmp_path, monkeypatch) -> None:
    from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs

    class _Boom(EvalMetric):
        metric_id = "toy_boom"
        description = "boom"
        requires_llm = False

        async def compute(self, inputs: RunInputs, ctx: MetricContext):
            raise RuntimeError("boom")

    events = _run_with_events(tmp_path)
    provider = get_provider(DEFAULT_METRIC_PROVIDER)
    provider.register_metric(ToyEchoMetric())
    provider.register_metric(_Boom())
    monkeypatch.setattr(
        "mas.library.eval.mce.runner.install_openai_llm_service",
        lambda *args, **kwargs: "gpt-4o",
    )
    step = EvalMceStep(
        name="eval_mce",
        config={
            "run_dir": str(events.parent.parent),
            "events_path": str(events),
            "metrics": ["toy_echo", "toy_boom"],
            "validate": False,
        },
    )
    out = await step.execute(_ctx(tmp_path))
    doc = json.loads(Path(out.metadata["output"]).read_text(encoding="utf-8"))
    assert doc["session"]["toy_echo"]["value"] == 1.0
    assert doc["session"]["toy_boom"]["value"] is None
    assert doc["run_quality"]["status"] == "error"
