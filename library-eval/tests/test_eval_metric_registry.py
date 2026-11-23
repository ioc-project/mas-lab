#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest
from mas.library.eval.evaluator import DEFAULT_METRIC_PROVIDER, get_provider
from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs
from mas.library.eval.metrics.plugins import reset_metrics_for_tests
from mas.library.eval.metrics.toy import ToyEchoMetric


class _BatchMetric(EvalMetric):
    calls = 0
    batch_key = "toy_batch"
    requires_llm = False
    description = "batched toy metric"

    def __init__(self, metric_id: str, value: float) -> None:
        self.metric_id = metric_id
        self._value = value

    async def compute(self, inputs: RunInputs, ctx: MetricContext):
        raise AssertionError("batch family must use compute_batch")

    @classmethod
    async def compute_batch(cls, metrics, inputs, ctx):
        cls.calls += 1
        return {
            m.metric_id: {
                "value": m._value,
                "reasoning": "batched",
                "error": None,
            }
            for m in metrics
        }


class _FailMetric(EvalMetric):
    metric_id = "toy_fail"
    description = "always fails"
    requires_llm = False

    async def compute(self, inputs: RunInputs, ctx: MetricContext):
        raise RuntimeError("intentional failure")


@pytest.fixture(autouse=True)
def _clean_hosted_metrics():
    reset_metrics_for_tests()
    yield
    reset_metrics_for_tests()


def _trace(tmp_path: Path) -> Path:
    path = tmp_path / "traces" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind":"user_message","content":"hi"}\n', encoding="utf-8")
    return path


def _host():
    return get_provider(DEFAULT_METRIC_PROVIDER)


@pytest.mark.asyncio
async def test_register_and_compute_toy_echo(tmp_path: Path) -> None:
    provider = _host()
    provider.register_metric(ToyEchoMetric())
    events = _trace(tmp_path)
    inputs = RunInputs(run_dir=tmp_path, native_trace=events)
    scores = await provider.compute_metrics(["toy_echo"], inputs, MetricContext())
    assert scores["toy_echo"]["value"] == 1.0
    assert scores["toy_echo"]["details"]["bytes"] > 0


def test_mce_provider_owns_stock_ids() -> None:
    provider = _host()
    assert provider.name == "mce"
    assert "groundedness" in provider.metric_ids()
    assert get_provider("mce_v1") is provider


def test_duplicate_id_raises() -> None:
    provider = _host()
    provider.register_metric(ToyEchoMetric())
    with pytest.raises(ValueError, match="Duplicate eval metric id"):
        provider.register_metric(ToyEchoMetric())


def test_stock_collision_raises() -> None:
    class _Groundedness(EvalMetric):
        metric_id = "groundedness"
        description = "collision"
        requires_llm = False

        async def compute(self, inputs, ctx):
            return {"value": 0.0, "reasoning": "", "error": None}

    provider = _host()
    with pytest.raises(ValueError, match="collides with stock MCE"):
        provider.register_metric(_Groundedness())


@pytest.mark.asyncio
async def test_compute_batch_called_once(tmp_path: Path) -> None:
    _BatchMetric.calls = 0
    provider = _host()
    provider.register_metric(_BatchMetric("toy_a", 0.25))
    provider.register_metric(_BatchMetric("toy_b", 0.75))
    events = _trace(tmp_path)
    scores = await provider.compute_metrics(
        ["toy_a", "toy_b"],
        RunInputs(run_dir=tmp_path, native_trace=events),
        MetricContext(),
    )
    assert _BatchMetric.calls == 1
    assert scores["toy_a"]["value"] == 0.25
    assert scores["toy_b"]["value"] == 0.75


@pytest.mark.asyncio
async def test_failure_isolation(tmp_path: Path) -> None:
    provider = _host()
    provider.register_metric(ToyEchoMetric())
    provider.register_metric(_FailMetric())
    events = _trace(tmp_path)
    scores = await provider.compute_metrics(
        ["toy_echo", "toy_fail"],
        RunInputs(run_dir=tmp_path, native_trace=events),
        MetricContext(),
    )
    assert scores["toy_echo"]["value"] == 1.0
    assert scores["toy_fail"]["value"] is None
    # The type name only: a metric's exception text may be a provider's body.
    assert scores["toy_fail"]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_unknown_id_lists_available(tmp_path: Path) -> None:
    provider = _host()
    provider.register_metric(ToyEchoMetric())
    events = _trace(tmp_path)
    with pytest.raises(ValueError, match="Unknown eval metric"):
        await provider.compute_metrics(
            ["not_a_metric"],
            RunInputs(run_dir=tmp_path, native_trace=events),
            MetricContext(),
        )


def test_metrics_schema_accepts_details() -> None:
    import jsonschema

    schema_path = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "lab" / "artefacts" / "metrics.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    doc = {
        "schema_version": "1",
        "item_id": "1",
        "scenario": "baseline",
        "session": {
            "toy_echo": {
                "value": 1.0,
                "reasoning": "ok",
                "error": None,
                "details": {"state": "absent"},
            }
        },
        "run_quality": {"warnings": [], "errors": [], "status": "ok"},
        "computed_at": "2026-10-07T00:00:00+00:00",
    }
    jsonschema.validate(doc, schema)
