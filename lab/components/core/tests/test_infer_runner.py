#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for manifest-driven lab runner inference."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
from mas.lab.runners.infer import (
    framework_adapter_from_dict,
    infer_runner_id,
    runner_id_for_framework_adapter,
)


def test_runner_id_for_framework_adapter_defaults_to_mas_lab() -> None:
    assert runner_id_for_framework_adapter(None) == DEFAULT_LAB_RUNNER_ID
    assert runner_id_for_framework_adapter("native") == DEFAULT_LAB_RUNNER_ID
    # Framework wrappers are not shipped in OSS — native mas machinery only.
    assert runner_id_for_framework_adapter("langgraph") == DEFAULT_LAB_RUNNER_ID
    assert runner_id_for_framework_adapter("autogen") == DEFAULT_LAB_RUNNER_ID


def test_framework_adapter_from_agent_spec() -> None:
    doc = {"spec": {"framework_adapter": "langgraph"}}
    assert framework_adapter_from_dict(doc) == "langgraph"


def test_framework_adapter_from_mas_framework_block() -> None:
    doc = {"spec": {"framework": {"default_adapter": "autogen"}}}
    assert framework_adapter_from_dict(doc) == "autogen"


def test_infer_runner_execution_override_wins(tmp_path: Path) -> None:
    mas = tmp_path / "mas.yaml"
    mas.write_text(
        yaml.safe_dump({"spec": {"framework": {"default_adapter": "langgraph"}}}),
        encoding="utf-8",
    )
    assert (
        infer_runner_id(
            execution_runner="native",
            mas_manifest=mas,
            agent_config={"spec": {"framework_adapter": "autogen"}},
        )
        == DEFAULT_LAB_RUNNER_ID
    )


def test_infer_runner_from_mas_manifest(tmp_path: Path) -> None:
    mas = tmp_path / "mas.yaml"
    mas.write_text(
        yaml.safe_dump({"spec": {"framework": {"default_adapter": "langgraph"}}}),
        encoding="utf-8",
    )
    assert infer_runner_id(mas_manifest=mas) == DEFAULT_LAB_RUNNER_ID


def test_infer_runner_from_app_agent_config() -> None:
    cfg = {"spec": {"framework_adapter": "autogen"}}
    assert infer_runner_id(agent_config=cfg) == DEFAULT_LAB_RUNNER_ID
