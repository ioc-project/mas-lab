#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process manifest validation (no subprocess)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from mas.lab.controller.manifest_validation import (
    refuse_secret_values,
    validate_manifest_yaml_content,
    validate_overlay_yaml_content,
)
from mas.runtime.spec.yaml_errors import YAML_ERROR_PREFIX


@pytest.mark.parametrize(
    "content",
    [
        "spec: !!set {sk-canary-value: null}\n",
        "spec: !!omap [{api_key: sk-canary-value}]\n",
        "spec: !!pairs [[api_key, sk-canary-value]]\n",
    ],
)
def test_collection_tags_are_refused_without_quoting_the_value(content: str):
    with pytest.raises(HTTPException) as raised:
        refuse_secret_values(content)
    assert raised.value.status_code == 422
    assert "sk-canary-value" not in str(raised.value.detail)


def test_validate_refuses_unreadable_yaml_by_position():
    # kills: an unreadable request answered as valid, or its detail quoting the line
    with pytest.raises(HTTPException) as raised:
        validate_manifest_yaml_content("api_key: sk-canary-value\n\tx: 1\n", base_dir=Path.cwd())
    assert raised.value.status_code == 422
    assert raised.value.detail.startswith(YAML_ERROR_PREFIX) and "sk-canary-value" not in raised.value.detail


def test_validate_agent_inline_ok():
    yaml = """
apiVersion: mas/v1
kind: Agent
metadata:
  name: t
spec:
  description: d
  context:
    role: i
  design_pattern:
    type: react
"""
    out = validate_manifest_yaml_content(yaml, base_dir=Path.cwd())
    assert out["valid"] is True
    assert out["exit_code"] == 0
    assert out["command"] == "validate_data"


def test_validate_agent_missing_name_fails():
    yaml = """
apiVersion: mas/v1
kind: Agent
metadata: {}
spec:
  description: d
  context:
    role: i
  design_pattern:
    type: react
"""
    out = validate_manifest_yaml_content(yaml, base_dir=Path.cwd())
    assert out["valid"] is False
    assert out["exit_code"] == 1


def test_validate_overlay_kind():
    yaml = """
apiVersion: mas/v1
kind: Overlay
metadata:
  name: ov
spec:
  agents: {}
"""
    errors = validate_overlay_yaml_content(yaml, base_dir=Path.cwd())
    assert errors is None or isinstance(errors, list)
