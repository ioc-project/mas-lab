#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Semantic manifest checks beyond JSON Schema: tool impl shape, agent tool
ref paths, unsupported overlay !append tags, and friendly YAML parse errors."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from mas.ctl.validate import validate_data, validate_file


def test_tool_missing_impl() -> None:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Tool",
        "metadata": {"name": "my_tool"},
        "spec": {"description": "My tool", "parameters": []},
    }
    result = validate_data(manifest, kind="tool", strict=True)
    assert not result.ok
    assert any("spec.impl is required" in issue.message for issue in result.issues)


def test_tool_missing_module_path() -> None:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Tool",
        "metadata": {"name": "my_tool"},
        "spec": {
            "description": "My tool",
            "parameters": [],
            "impl": {"kind": "python", "class_name": "MyTool"},
        },
    }
    result = validate_data(manifest, kind="tool", strict=True)
    assert not result.ok
    assert any("module_path is required" in issue.message for issue in result.issues)


def test_tool_legacy_implementation_key() -> None:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Tool",
        "metadata": {"name": "my_tool"},
        "spec": {
            "description": "My tool",
            "parameters": [],
            "implementation": {"type": "mock", "response": "{}"},
        },
    }
    result = validate_data(manifest, kind="tool", strict=True)
    assert not result.ok
    assert any("spec.implementation is not supported" in issue.message for issue in result.issues)


def test_agent_tool_ref_without_dot_slash() -> None:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "my_agent"},
        "spec": {
            "description": "My agent",
            "tools": [{"ref": "tools/my_tool.yaml"}],  # missing ./
            "models": [{"model": "azure/gpt-4o"}],
            "design_pattern": {"type": "react"},
        },
    }
    result = validate_data(manifest, kind="agent", strict=True)
    assert not result.ok
    assert any("looks like a file path" in issue.message and "use './...'" in issue.message for issue in result.issues)


def test_agent_tool_ref_with_correct_format() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        tool_dir = tmppath / "tools"
        tool_dir.mkdir()
        (tool_dir / "my_tool.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "mas/v1",
                    "kind": "Tool",
                    "metadata": {"name": "my_tool"},
                    "spec": {
                        "description": "My tool",
                        "parameters": [],
                        "impl": {
                            "kind": "python",
                            "module_path": "./my_tool.py",
                            "class_name": "MyTool",
                        },
                    },
                }
            )
        )

        agent_file = tmppath / "agent.yaml"
        agent_file.write_text(
            yaml.dump(
                {
                    "apiVersion": "mas/v1",
                    "kind": "Agent",
                    "metadata": {"name": "my_agent"},
                    "spec": {
                        "description": "My agent",
                        "tools": [{"ref": "./tools/my_tool.yaml"}],
                        "models": [{"model": "azure/gpt-4o"}],
                        "design_pattern": {"type": "react"},
                    },
                }
            )
        )

        result = validate_file(agent_file, kind="agent", strict=True, resolve_refs=True)
        assert result.ok, f"Validation failed: {[i.message for i in result.issues]}"


def test_overlay_with_append_tag_fails_fast_at_parse_time() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        overlay_file = Path(tmpdir) / "overlay.yaml"
        overlay_file.write_text(
            """
apiVersion: mas/v1
kind: Overlay
metadata:
  name: test-overlay
spec:
  target:
    kind: MAS
  patch:
    agents:
      my_agent:
        context:
          role: !append |
            Additional instructions
"""
        )

        result = validate_file(overlay_file, kind="overlay", strict=True, resolve_refs=False)
        assert not result.ok
        assert any("!append" in issue.message for issue in result.issues)


def test_overlay_with_op_add() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        overlay_file = Path(tmpdir) / "overlay.yaml"
        overlay_file.write_text(
            """
apiVersion: mas/v1
kind: Overlay
metadata:
  name: test-overlay
spec:
  target:
    kind: MAS
  patch:
    agents:
      my_agent:
        context:
          role:
            $op:
              add:
                - |
                  Additional instructions to append
"""
        )

        result = validate_file(overlay_file, kind="overlay", strict=True, resolve_refs=False)
        assert result.ok, f"Validation failed: {[i.message for i in result.issues]}"


def test_agent_bare_string_tool_ref_with_path() -> None:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "my_agent"},
        "spec": {
            "description": "My agent",
            "tools": ["tools/query_salesforce_opportunity.yaml"],  # should be ./tools/...
            "models": [{"model": "azure/gpt-4o"}],
            "design_pattern": {"type": "react"},
        },
    }
    result = validate_data(manifest, kind="agent", strict=True)
    assert not result.ok
    assert any("looks like a file path" in issue.message and "use './...'" in issue.message for issue in result.issues)
