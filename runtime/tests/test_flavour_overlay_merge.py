#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for merge_flavour_overlay + merge_overlay's Flavour dispatch (FT7).

See docs/design/flavour-boundary.md — a Flavour-targeted overlay may only
patch deployment-posture keys; observability/control use the same
plugin-list merge semantics as agent overlays.
"""

import pytest
from mas.ctl.overlay.merge import OverlayTargetError, merge_flavour_overlay, merge_overlay


def _flavour(spec: dict | None = None) -> dict:
    return {
        "apiVersion": "flavour/v1",
        "kind": "Flavour",
        "metadata": {"name": "local"},
        "spec": spec or {},
    }


def _overlay(patch: dict, *, target_kind: str | None = "Flavour") -> dict:
    spec: dict = {"patch": patch}
    if target_kind is not None:
        spec["target"] = {"kind": target_kind}
    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "test"},
        "spec": spec,
    }


class TestMergeOverlayDispatchesToFlavour:
    def test_target_kind_flavour_routes_to_flavour_merge(self):
        base = _flavour({"observability": ["native"]})
        merged = merge_overlay(base, _overlay({"observability": {"$op": {"replace": ["otel"]}}}))
        assert merged["spec"]["observability"] == ["otel"]

    def test_missing_target_is_rejected(self):
        base = _flavour({"observability": ["native"]})
        with pytest.raises(OverlayTargetError):
            merge_overlay(base, _overlay({"observability": {"$op": {"replace": ["otel"]}}}, target_kind=None))

    def test_does_not_fall_through_to_agent_merge(self):
        # merge_agent_overlay would happily attach spec.design_pattern; a
        # Flavour base must go through merge_flavour_overlay instead, which
        # rejects it (design_pattern isn't deployment posture).
        base = _flavour({})
        with pytest.raises(OverlayTargetError):
            merge_overlay(base, _overlay({"design_pattern": {"type": "cot"}}, target_kind=None))


class TestMergeFlavourOverlayWhitelist:
    def test_observability_list_replaces_base(self):
        base = _flavour({"observability": ["native"]})
        merged = merge_flavour_overlay(base, _overlay({"observability": {"$op": {"replace": ["native", "otel"]}}}))
        assert merged["spec"]["observability"] == ["native", "otel"]

    def test_observability_dict_patch_merges_native_config(self):
        base = _flavour({"observability": [{"native": {"path": "traces/a.jsonl"}}]})
        merged = merge_flavour_overlay(
            base, _overlay({"observability": {"$op": {"replace": [{"native": {"path": "traces/b.jsonl"}}]}}})
        )
        assert merged["spec"]["observability"] == [{"native": {"path": "traces/b.jsonl"}}]

    def test_control_uses_same_plugin_list_semantics(self):
        base = _flavour({})
        merged = merge_flavour_overlay(
            base, _overlay({"control": {"$op": {"merge": {"budget": {"max_tokens": 1000}}}}})
        )
        assert merged["spec"]["control"]["budget"]["max_tokens"] == 1000

    def test_agent_comm_dict_merges(self):
        base = _flavour({"agent_comm": {"protocol": "agent-local", "mode": "local"}})
        merged = merge_flavour_overlay(base, _overlay({"agent_comm": {"mode": "remote"}}))
        assert merged["spec"]["agent_comm"] == {"protocol": "agent-local", "mode": "remote"}

    @pytest.mark.parametrize("key", ["llm", "skills", "prefer_local", "memory", "tools_remove"])
    def test_forbidden_keys_rejected(self, key):
        base = _flavour({})
        with pytest.raises(OverlayTargetError):
            merge_flavour_overlay(base, _overlay({key: {"anything": True}}))

    def test_no_overlay_spec_is_a_noop(self):
        base = _flavour({"observability": ["native"]})
        merged = merge_flavour_overlay(base, {"metadata": {"name": "empty"}})
        assert merged["spec"]["observability"] == ["native"]
