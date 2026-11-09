#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance binding — plugin list only."""

import pytest
from mas.ctl.manifest.spec_bindings import SpecBindingError, parse_governance


def test_governance_plugin_list():
    raw = [{"sample_governance": {"hitl_on_tool": True, "policies": []}}]
    binding = parse_governance(raw)
    assert binding.plugins == ["sample_governance"]
    assert binding.hitl_on_tool is True


def test_governance_error_policy_flattened():
    raw = [
        {
            "retry_on_error": {
                "error_recovery_plugin": "retry_on_error",
                "error_policy": {"transient": "retry", "fatal": "block"},
            }
        }
    ]
    binding = parse_governance(raw)
    assert binding.error_recovery_plugin == "retry_on_error"
    assert binding.error_policy["transient"] == "retry"


def test_governance_flat_dict_rejected():
    with pytest.raises(SpecBindingError, match="plugin list"):
        parse_governance({"hitl_on_tool": True})


def test_control_retry_unknown_field_rejected():
    from mas.ctl.manifest.spec_bindings import parse_control

    with pytest.raises(SpecBindingError, match="unknown field"):
        parse_control({"retry": {"http": {}}})
