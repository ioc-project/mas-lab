#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""build_cli_overlay must emit a canonical mas/v1 Overlay (spec.patch), so the
--tool/--skill/--memory/--set inline flags merge instead of tripping
normalize_overlay's shape check."""

from __future__ import annotations

import pytest
from mas.ctl.overlay.merge import merge_overlay
from mas.ctl.overlay.normalize import normalize_overlay
from mas.ctl.runtime_cli import build_cli_overlay, load_merged_agent_manifest


def test_build_cli_overlay_none_when_empty() -> None:
    assert build_cli_overlay() is None


def test_build_cli_overlay_tools_branch() -> None:
    ov = build_cli_overlay(tools=("web-search", "calculator"))
    assert ov["spec"]["target"]["kind"] == "Agent"
    assert ov["spec"]["patch"]["tools"] == {"$op": {"add": ["web-search", "calculator"]}}


def test_build_cli_overlay_bad_set_raises() -> None:
    with pytest.raises(ValueError, match="KEY=VALUE"):
        build_cli_overlay(set_values=("noequals",))


def test_build_cli_overlay_is_canonical_overlay() -> None:
    ov = build_cli_overlay(skills=("answer-formatting",), memory="semantic", set_values=("k=v",))
    assert ov["apiVersion"] == "mas/v1"
    assert ov["kind"] == "Overlay"
    patch = ov["spec"]["patch"]
    assert ov["spec"]["target"]["kind"] == "Agent"
    assert patch["skills"] == {"$op": {"add": ["answer-formatting"]}}
    assert patch["memory"] == "semantic"
    assert patch["context"] == {"k": "v"}
    # accepted by normalize_overlay (the check that previously rejected it)
    assert normalize_overlay(ov) is not None


def test_cli_overlay_merges_into_agent_manifest() -> None:
    base = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {"context": {"intent": "x"}},
    }
    ov = build_cli_overlay(memory="semantic", set_values=("tone=terse",))
    merged = merge_overlay(base, ov)
    assert merged["spec"]["memory"] == "semantic"
    assert merged["spec"]["context"]["tone"] == "terse"
    assert merged["spec"]["context"]["intent"] == "x"  # preserved


def test_general_override_has_priority_over_legacy_inline_flags() -> None:
    base = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {"context": {"tone": "formal"}},
    }
    data, _ = load_merged_agent_manifest(
        base,
        memory="semantic",
        set_values=("tone=terse",),
        overrides=("agent:spec.context.tone=direct",),
        validate=False,
    )

    assert data["spec"]["memory"] == "semantic"
    assert data["spec"]["context"]["tone"] == "direct"


def test_no_manifest_cli_only_builds_agent(tmp_path) -> None:
    # `mas-ctl chat --skill ... --memory ...` with no YAML manifest yields a
    # real kind: Agent, not a bare Overlay document.
    data, plugin = load_merged_agent_manifest(None, skills=("answer-formatting",), memory="semantic", validate=False)
    assert data["kind"] == "Agent"
    assert data["spec"]["memory"] == "semantic"
    assert data["spec"]["skills"] == ["answer-formatting"]
    assert plugin == "react@v1"
