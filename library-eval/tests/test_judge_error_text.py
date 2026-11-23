#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A failed judge call or metric reads '<Type>: HTTP <status>', never the provider's body (which may echo a key)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace

import httpx
import mas.library.eval.mce.runner as runner
import pytest
from click.testing import CliRunner
from mas.library.eval.cli import eval_cmd
from mas.library.eval.errors import JudgeCallFailed, judge_error_text
from mas.library.eval.evaluator import DEFAULT_METRIC_PROVIDER, get_provider
from mas.library.eval.mce import registry_api
from mas.library.eval.metrics import EvalMetric, MetricContext, RunInputs
from mas.library.eval.metrics.llm_json import complete_json
from mas.library.eval.metrics.plugins import reset_metrics_for_tests
from mas.library.eval.providers.adversarial import AdversarialProvider
from mas.runtime.reliability.classes import FailureClass

BODY = "echo of Bearer sk-LEAK"


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(BODY)
        self.response = SimpleNamespace(status_code=status, headers={})


def test_error_text_hides_the_body_but_keeps_our_own_messages() -> None:
    assert judge_error_text(_HttpError(401)) == "_HttpError: HTTP 401"
    assert judge_error_text(ValueError(BODY)) == "ValueError"
    assert judge_error_text(JudgeCallFailed("ours")) == "ours"


@pytest.mark.parametrize(
    ("exc", "failure_class", "code"),
    [
        (_HttpError(429), FailureClass.TRANSIENT, "RATE_LIMIT"),
        (_HttpError(503), FailureClass.TRANSIENT, None),
        (_HttpError(401), FailureClass.APPLICATION, "ERROR"),
        (httpx.ConnectError(BODY), FailureClass.TRANSIENT, "TRANSPORT"),
        (ValueError(BODY), FailureClass.APPLICATION, "ERROR"),
    ],
)
def test_classification_never_carries_the_body(exc, failure_class, code) -> None:
    failure = runner._classify_mce_failure(exc)
    assert failure.failure_class is failure_class
    assert code is None or failure.code == code
    assert "sk-LEAK" not in str(failure)


@pytest.fixture
def installed(monkeypatch):
    service = runner._JudgeService(model="m", base_url="http://judge.test/v1", api_key=SimpleNamespace())
    monkeypatch.setattr(runner, "_service", service)
    monkeypatch.setattr(runner, "_jury", object())
    monkeypatch.setattr(runner, "judge_client", lambda: contextlib.nullcontext(SimpleNamespace(close=lambda: None)))
    return service


def test_paced_call_uses_the_installed_host(installed) -> None:
    assert runner.paced_live_call(lambda: "ok", model="m") == "ok"


def test_deepeval_failures_are_reported_without_the_body(installed, monkeypatch) -> None:
    monkeypatch.setattr("mas.library.standard.lib.openai_client.reveal_openai_key", lambda *_a: "k")
    real_make = runner._make_deepeval_model
    monkeypatch.setattr(runner, "_make_deepeval_model", lambda **_kw: None)
    assert runner._compute_deepeval_score("goal_success_rate", "q", "a")["error"] == "deepeval model unavailable"

    monkeypatch.setattr(runner, "_make_deepeval_model", lambda **_kw: object())
    monkeypatch.setattr("deepeval.test_case.LLMTestCase", lambda **_kw: (_ for _ in ()).throw(_HttpError(500)))
    assert runner._compute_deepeval_score("answer_relevancy", "q", "a")["error"] == "_HttpError: HTTP 500"

    monkeypatch.setattr(runner, "_deepeval_llm_cls", lambda: (_ for _ in ()).throw(_HttpError(401)))
    assert real_make(model="m", api_key="k", base_url=None) is None


def test_session_metrics_report_each_failure_as_type_and_status(installed, monkeypatch, tmp_path) -> None:
    context = {"input_query": "q", "final_response": "", "conversation_text": "hi"}
    monkeypatch.setattr(
        "mas.library.eval.mce.trace_provider.MASTraceProvider",
        lambda **_kw: SimpleNamespace(fetch=lambda *_a, **_k: dict(context)),
    )

    async def fake_run(name, _session, jury=None):
        if name == "boom":
            raise _HttpError(500)
        if name == "gone":
            return None
        return SimpleNamespace(value=1, reasoning="r", error_message=None)

    monkeypatch.setattr(runner, "run_mce_class", fake_run)
    out = runner.compute_session_metrics(
        tmp_path / "events.jsonl", ["goal_success_rate", "boom", "gone", "fine"], binary_grading=False
    )
    assert out["goal_success_rate"]["error"] == "final_response_empty"
    assert out["boom"]["error"] == "_HttpError: HTTP 500"
    assert out["gone"]["error"] == "import failed: gone"
    assert out["fine"]["value"] == 1.0
    assert out["__run_quality__"]["status"] == "error"

    monkeypatch.setattr(runner, "_compute_deepeval_score", lambda *_a: {"value": 0.5, "reasoning": "", "error": None})
    context["final_response"] = "a"
    out = runner.compute_session_metrics(tmp_path / "events.jsonl", ["answer_relevancy"])
    assert out["answer_relevancy"]["value"] == 0.5

    context["input_query"] = ""
    out = runner.compute_session_metrics(tmp_path / "events.jsonl", ["answer_relevancy"])
    assert out["answer_relevancy"]["error"] == "input_query_empty"


def test_jury_query_failure_is_a_judge_call_failed(installed, monkeypatch) -> None:
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**_kw):
                    raise _HttpError(401)

    monkeypatch.setattr(runner, "judge_client", lambda: contextlib.nullcontext(Client))
    with pytest.raises(JudgeCallFailed, match="_HttpError: HTTP 401") as info:
        runner._jury_query(SimpleNamespace(model=None), [{"role": "user", "content": "x"}])
    assert "sk-LEAK" not in str(info.value)


class _BatchBoom(EvalMetric):
    batch_key = "boom_batch"
    metric_id = "boom_batch_a"
    requires_llm = False
    description = "fails as a batch"

    async def compute(self, inputs, ctx):
        raise AssertionError("batched")

    @classmethod
    async def compute_batch(cls, metrics, inputs, ctx):
        raise _HttpError(502)


def test_failing_batch_surfaces_type_and_status(tmp_path) -> None:
    reset_metrics_for_tests()
    provider = get_provider(DEFAULT_METRIC_PROVIDER)
    provider.register_metric(_BatchBoom())
    try:
        scores = asyncio.run(provider.compute_metrics(["boom_batch_a"], RunInputs(run_dir=tmp_path), MetricContext()))
    finally:
        reset_metrics_for_tests()
    assert scores["boom_batch_a"]["error"] == "_HttpError: HTTP 502"


async def _registry_failure(monkeypatch):
    async def boom(*_a, **_k):
        raise _HttpError(500)

    monkeypatch.setattr(registry_api, "run_mce_class", boom)
    return await registry_api.compute_session_metrics(
        SimpleNamespace(app_name="a", session_id="s"),
        ["m"],
        {"LLM_MODEL_NAME": "m", "LLM_BASE_MODEL_URL": "", "LLM_API_KEY": ""},
    )


def test_registry_failure_message(monkeypatch) -> None:
    (result,) = asyncio.run(_registry_failure(monkeypatch))
    assert result.error_message == "_HttpError: HTTP 500"
    assert result.description == "Computation failed: _HttpError: HTTP 500"


def test_cli_scores_a_trace_without_a_key_option(monkeypatch, tmp_path) -> None:
    trace = tmp_path / "events.jsonl"
    trace.write_text("{}\n")
    monkeypatch.setattr("mas.library.eval.cli.install_openai_llm_service", lambda *_a, **_k: "m")
    monkeypatch.setattr(
        "mas.library.eval.cli.compute_session_metrics",
        lambda *_a, **_k: {"goal_success_rate": {"value": 1.0, "reasoning": "", "error": None}},
    )
    result = CliRunner().invoke(eval_cmd, [str(trace), "--json"])
    assert json.loads(result.output) == {"goal_success_rate": {"value": 1.0, "reasoning": "", "error": None}}
    assert eval_cmd.params and "api_key_env" not in {p.name for p in eval_cmd.params}


def test_complete_json_reports_a_failed_call_without_the_body(installed, monkeypatch) -> None:
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    if "response_format" in kw:
                        raise ValueError("no json mode")
                    raise _HttpError(401)

    monkeypatch.setattr(runner, "judge_client", lambda: contextlib.nullcontext(Client))
    with pytest.raises(JudgeCallFailed, match="_HttpError: HTTP 401") as info:
        complete_json([{"role": "user", "content": "x"}])
    assert "sk-LEAK" not in str(info.value)


def test_complete_json_gives_up_on_malformed_replies(installed, monkeypatch) -> None:
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: reply)))
    monkeypatch.setattr(runner, "judge_client", lambda: contextlib.nullcontext(client))
    with pytest.raises(JudgeCallFailed, match="did not return valid JSON"):
        complete_json([{"role": "user", "content": "x"}], retries=1)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [('{"score": 3, "reasoning": "r"}', {"score": 1.0, "reasoning": "r"}), ("oops", None)],
)
def test_adversarial_judge_clips_scores_and_hides_failures(tmp_path, monkeypatch, reply, expected) -> None:
    dataset = tmp_path / "d.yaml"
    dataset.write_text('{"items": []}')
    judge = AdversarialProvider(dataset_path=dataset)

    def create(**_kw):
        if reply == "oops":
            raise _HttpError(500)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(judge, "_get_llm_client", lambda: client)
    out = judge._llm_judge("p")
    if expected:
        assert out == expected
    else:
        assert out["error"] == "_HttpError: HTTP 500" and out["score"] is None
