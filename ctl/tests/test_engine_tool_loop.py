#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tool egress is structural (spec.tools + pattern), not a governance boolean."""

from __future__ import annotations

from mas.ctl.session.manifest_config import engine_use_tool_loop, kernel_config_from_manifest


def test_declared_tools_enable_llm_tool_catalog():
    manifest = {
        "spec": {
            "tools": ["lookup", "summarize"],
        }
    }
    kernel = kernel_config_from_manifest(manifest)
    assert engine_use_tool_loop(manifest, kernel) is True


def test_no_tools_means_no_llm_tool_catalog():
    manifest = {"spec": {"context": {"role": "helper"}}}
    kernel = kernel_config_from_manifest(manifest)
    assert engine_use_tool_loop(manifest, kernel) is False


def test_declared_skills_enable_llm_tool_catalog():
    """Listing skills injects activate_skill; the LLM must see that tool."""
    manifest = {"spec": {"skills": ["answer-formatting"]}}
    kernel = kernel_config_from_manifest(manifest)
    assert engine_use_tool_loop(manifest, kernel) is True


def test_plan_execute_pattern_uses_dp_tool_scheduling():
    manifest = {"spec": {"tools": ["lookup"]}}
    kernel = kernel_config_from_manifest(manifest, pattern_plugin_id="plan_execute@v1")
    assert engine_use_tool_loop(manifest, kernel) is False


def test_deprecated_kernel_config_path_does_not_take_dead_ingress_specs():
    import inspect

    from mas.ctl.session.ingress_governance_loader import build_ingress_governance_plugins

    params = inspect.signature(build_ingress_governance_plugins).parameters
    assert "ingress_plugin_specs" not in params
    kernel = kernel_config_from_manifest({"spec": {}})
    assert kernel is not None


def test_mcp_selection_enables_llm_tool_catalog():
    manifest = {"spec": {"mcp_tools": [{"server_ref": "records", "include": {"prefixes": ["read_"]}}]}}
    assert engine_use_tool_loop(manifest, kernel_config_from_manifest(manifest)) is True
    assert (
        engine_use_tool_loop(manifest, kernel_config_from_manifest(manifest, pattern_plugin_id="plan_execute@v1"))
        is False
    )


def test_empty_mcp_selection_does_not_enable_llm_tool_catalog():
    manifest = {"spec": {"mcp_tools": []}}
    assert engine_use_tool_loop(manifest, kernel_config_from_manifest(manifest)) is False
