#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""AdversarialProvider: metric dispatch, judge prompts and client setup."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mas.library.eval.providers.adversarial import AdversarialProvider

_ITEMS = [{"id": item_id, "prompt": f"task for {item_id}"} for item_id in ("mn10", "mn11", "mn12")]
_METRIC_FOR = {"mn10": "budget_respected", "mn11": "fare_accuracy", "mn12": "fare_error_caught"}


@pytest.fixture
def provider(tmp_path: Path, monkeypatch) -> tuple[AdversarialProvider, list[str]]:
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(json.dumps({"items": _ITEMS}), encoding="utf-8")
    prompts: list[str] = []
    judge = AdversarialProvider(dataset_path=dataset)

    def fake_judge(prompt: str, *, temperature: float = 0.0) -> dict:
        prompts.append(prompt)
        return {"score": 1.0, "reasoning": "ok"}

    monkeypatch.setattr(judge, "_llm_judge", fake_judge)
    return judge, prompts


@pytest.mark.parametrize("item_id", sorted(_METRIC_FOR))
def test_compute_from_text_judges_each_item_with_its_metric(provider, item_id: str) -> None:
    judge, prompts = provider
    out = judge.compute_from_text("the answer", item_id, [_METRIC_FOR[item_id], "nope"])
    assert out[_METRIC_FOR[item_id]]["value"] == 1.0
    assert out["nope"]["error"] == "Unknown metric: nope"
    assert len(prompts) == 1
    assert f"task for {item_id}" in prompts[0]
    assert "the answer" in prompts[0]


def test_compute_from_text_reports_an_item_that_is_not_in_the_dataset(provider) -> None:
    judge, prompts = provider
    out = judge.compute_from_text("x", "mn99", ["budget_respected"])
    assert out["budget_respected"]["value"] is None
    assert "mn99" in out["budget_respected"]["error"]
    assert not prompts


@pytest.mark.parametrize("item_id", sorted(_METRIC_FOR))
def test_compute_reads_the_kg_and_dispatches_by_metric(provider, tmp_path: Path, monkeypatch, item_id: str) -> None:
    judge, prompts = provider
    kg_path = tmp_path / "kg.json"
    kg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(judge, "_extract_output", lambda kg, agent: "kg answer")
    monkeypatch.setattr(judge, "_extract_item_id", lambda path, kg: item_id)
    monkeypatch.setattr(judge, "_load_dataset", lambda path: {"items": _ITEMS})
    out = judge.compute(kg_path, [_METRIC_FOR[item_id], "nope"])
    assert out[_METRIC_FOR[item_id]]["value"] == 1.0
    assert out["nope"]["error"] == "Unknown metric: nope"
    assert "kg answer" in prompts[0]


def test_the_client_needs_the_openai_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ImportError, match="pip install openai"):
        AdversarialProvider()._get_llm_client()


def test_the_client_needs_the_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        AdversarialProvider()._get_llm_client()
