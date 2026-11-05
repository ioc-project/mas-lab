#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from mas.library.standard.plugins.context.summarizer import SUMMARIZE_INSTRUCTIONS, LlmSummarizer
from mas.runtime.contracts.cm_factory import CMFactory

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "context" / "summarizer-override"


def _agent() -> dict:
    return yaml.safe_load((SAMPLE / "agent.yaml").read_text(encoding="utf-8"))


def _expected_instructions() -> str:
    params = _agent()["spec"]["context_manager"]["params"]["summarizer"]["params"]
    return str(params["instructions"]).strip()


def test_sample_files_exist() -> None:
    assert (SAMPLE / "agent.yaml").is_file()
    assert (SAMPLE / "summarize_call.json").is_file()
    assert (SAMPLE / "README.md").is_file()
    readme = (SAMPLE / "README.md").read_text(encoding="utf-8")
    assert "library-standard/examples/context/summarizer-override" in readme
    assert "library-samples/apps" not in readme
    assert "params.instructions" in readme
    assert "token budget" in readme.lower() or "history token" in readme


def test_sample_agent_pins_model_and_instructions() -> None:
    spec = _agent()["spec"]
    summ = spec["context_manager"]["params"]["summarizer"]
    assert summ["type"] == "llm"
    assert summ["params"]["model"] == "summarizer"
    assert "Preserve city names" in summ["params"]["instructions"]
    ids = {m["id"]: m["model"] for m in spec["models"]}
    assert ids["summarizer"] == "gpt-4o-mini"


def test_sample_agent_validates() -> None:
    pytest.importorskip("jsonschema")
    from mas.ctl.validate import validate_file

    result = validate_file(SAMPLE / "agent.yaml", kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_sample_payload_uses_example_instructions() -> None:
    payload = json.loads((SAMPLE / "summarize_call.json").read_text(encoding="utf-8"))
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"].strip() == _expected_instructions()
    assert payload["messages"][0]["content"].strip() != SUMMARIZE_INSTRUCTIONS


class _Engine:
    model = "gpt-4o"

    def __init__(self) -> None:
        self.seen: list[tuple[list, str | None]] = []

    def summarize_messages(self, messages, *, model=None):
        self.seen.append((messages, model))
        return "ok"


def test_factory_loads_example_model_and_instructions() -> None:
    engine = _Engine()
    cm = CMFactory.create(manifest=_agent(), engine=engine)
    plugin = cm._summarizer
    assert isinstance(plugin, LlmSummarizer)
    assert plugin.model == "gpt-4o-mini"
    assert plugin.instructions == _expected_instructions()
    text = plugin.summarize([{"role": "user", "content": "hi"}])
    assert text == "ok"
    assert engine.seen[0][0][0]["content"] == _expected_instructions()
    assert engine.seen[0][1] == "gpt-4o-mini"


def test_blank_instructions_keep_package_default() -> None:
    plugin = LlmSummarizer(instructions="  ")
    assert plugin.instructions == SUMMARIZE_INSTRUCTIONS
