#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""GenerateDatasetStep takes its endpoint from config and its key from the secret store."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mas.library.lab.steps.data import generate_dataset as gd
from secret_fakes import bound_secrets


def _run(monkeypatch, tmp_path, **config):
    seen: dict = {}

    def fake_call(prompt, model, api_base, api_key, temperature):
        seen.update(prompt=prompt, api_base=api_base, api_key=api_key)
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(gd, "_call_llm", fake_call)
    ctx = SimpleNamespace(pipeline=SimpleNamespace(config_path=None), output_dir=tmp_path)
    return asyncio.run(gd.GenerateDatasetStep(name="g", config=config).execute(ctx)), seen


def test_endpoint_and_key_are_read_from_the_host_and_secret_store(monkeypatch, tmp_path) -> None:
    with bound_secrets({"openai": "k-2"}):
        out, seen = _run(
            monkeypatch,
            tmp_path,
            api_key={"secret": "openai"},
            api_base="https://llm.example/v1",
            topic_hint="travel",
        )
    assert seen["api_base"] == "https://llm.example/v1" and seen["api_key"] == "k-2"
    assert "Additional constraint: travel" in seen["prompt"]
    # the error says the type only, never the client's text (it may hold the provider's body)
    assert out.metadata["success"] is False and out.data["error"] == "RuntimeError"


def test_a_missing_or_unreadable_intent_source_falls_back_to_a_generic_prompt(monkeypatch, tmp_path) -> None:
    with bound_secrets({"openai": "k"}):
        missing, seen = _run(monkeypatch, tmp_path, intent_source=str(tmp_path / "nope.yaml"))
    assert "general-purpose AI question-answering assistant" in seen["prompt"]
    assert missing.metadata["success"] is False

    broken = tmp_path / "intent.yaml"
    broken.write_text(":\n  - [unclosed", encoding="utf-8")
    with bound_secrets({"openai": "k"}):
        _, seen = _run(monkeypatch, tmp_path, intent_source=str(broken))
    assert "general-purpose AI question-answering assistant" in seen["prompt"]


@pytest.mark.no_secrets
def test_a_missing_required_secret_refuses_the_step(monkeypatch, tmp_path) -> None:
    from mas.runtime.secrets.errors import SecretMissing

    with pytest.raises(SecretMissing, match="openai"):
        _run(monkeypatch, tmp_path, api_key={"secret": "openai"}, api_base="http://x")


def test_questions_parse_from_a_json_list_or_plain_lines() -> None:
    assert gd._parse_questions('["a", "b", "c"]', 2) == ["a", "b"]
    assert gd._parse_questions("1. first\n- second\n# note\n", 5) == ["first", "second"]
