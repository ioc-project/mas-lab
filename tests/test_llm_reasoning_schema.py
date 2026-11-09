#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""JSON Schema coverage for spec.models[].reasoning."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate import validate_file


def _write_agent(tmp_path: Path, models: list) -> Path:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "probe"},
        "spec": {"description": "probe agent", "models": models},
    }
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return path


def test_models_reasoning_block_validates(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write_agent(
        tmp_path,
        [
            {
                "model": "gpt-5-mini",
                "kind": "openai",
                "max_tokens": 1500,
                "reasoning": {"effort": "low", "budget_tokens": 512, "exclude": True},
            }
        ],
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_models_reasoning_mode_pro_validates(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write_agent(
        tmp_path,
        [{"model": "gpt-5", "reasoning": {"effort": "high", "mode": "pro", "think": True}}],
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_models_reasoning_unknown_field_is_rejected(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write_agent(
        tmp_path,
        [{"model": "gpt-5", "reasoning": {"effort": "high", "turbo": True}}],
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok


def test_example_reasoning_overlay_validates() -> None:
    pytest.importorskip("jsonschema")
    path = Path(__file__).resolve().parents[1] / "docs/schemas/examples/overlays/llm-reasoning.yaml"
    result = validate_file(path, kind="overlay", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_example_sampling_overlay_validates() -> None:
    pytest.importorskip("jsonschema")
    path = Path(__file__).resolve().parents[1] / "docs/schemas/examples/overlays/llm-sampling.yaml"
    result = validate_file(path, kind="overlay", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_models_sampling_overset_validates(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write_agent(
        tmp_path,
        [
            {
                "model": "onprem/gemma4",
                "kind": "openai",
                "min_p": 0.05,
                "repetition_penalty": 1.05,
                "max_completion_tokens": 512,
                "extra": {"guided_json": {"type": "object"}},
                "reasoning": {"think": True, "exclude": True},
            }
        ],
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_catalog_yaml_matches_schema() -> None:
    pytest.importorskip("jsonschema")
    import jsonschema

    root = Path(__file__).resolve().parents[1]
    schema = yaml.safe_load((root / "docs/schemas/runtime/llm-model-catalog.schema.yaml").read_text())
    data = yaml.safe_load((root / "runtime/src/mas/runtime/spec/llm-model-catalog.yaml").read_text())
    jsonschema.Draft7Validator(schema).validate(data)
