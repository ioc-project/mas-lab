#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""GenerateDatasetStep takes its endpoint and key from the host environment."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mas.library.lab.steps.data import generate_dataset as gd
from mas.runtime import hostenv


def _run(monkeypatch, tmp_path, **config):
    seen: dict = {}

    def fake_call(prompt, model, api_base, api_key, temperature):
        seen.update(prompt=prompt, api_base=api_base, api_key=api_key)
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(gd, "_call_llm", fake_call)
    ctx = SimpleNamespace(pipeline=SimpleNamespace(config_path=None), output_dir=tmp_path)
    return asyncio.run(gd.GenerateDatasetStep(name="g", config=config).execute(ctx)), seen


def test_endpoint_and_key_are_read_from_the_host_environment(monkeypatch, tmp_path) -> None:
    with hostenv.overlay({"OPENAI_BASE_URL": "http://llm.example/v1", "GEN_KEY_PROBE": "k-2"}):
        out, seen = _run(monkeypatch, tmp_path, api_key_env="GEN_KEY_PROBE", topic_hint="travel")
    assert seen["api_base"] == "http://llm.example/v1" and seen["api_key"] == "k-2"
    assert "Additional constraint: travel" in seen["prompt"]
    assert out.metadata["success"] is False and "no network" in out.data["error"]


def test_a_missing_or_unreadable_intent_source_falls_back_to_a_generic_prompt(monkeypatch, tmp_path) -> None:
    missing, seen = _run(monkeypatch, tmp_path, intent_source=str(tmp_path / "nope.yaml"))
    assert "general-purpose AI question-answering assistant" in seen["prompt"]
    assert missing.metadata["success"] is False

    broken = tmp_path / "intent.yaml"
    broken.write_text(":\n  - [unclosed", encoding="utf-8")
    _, seen = _run(monkeypatch, tmp_path, intent_source=str(broken))
    assert "general-purpose AI question-answering assistant" in seen["prompt"]


def test_questions_parse_from_a_json_list_or_plain_lines() -> None:
    assert gd._parse_questions('["a", "b", "c"]', 2) == ["a", "b"]
    assert gd._parse_questions("1. first\n- second\n# note\n", 5) == ["first", "second"]
