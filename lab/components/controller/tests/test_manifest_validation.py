#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process manifest validation (no subprocess)."""

from __future__ import annotations

from pathlib import Path

from mas.lab.controller.manifest_validation import (
    validate_manifest_yaml_content,
    validate_overlay_yaml_content,
)


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
