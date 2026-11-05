#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""governance-binding.schema.yaml validates shape only — no plugin-id enum.

Regression: the schema used to `enum: [sample_governance, sample_governance@v1]`
(plus per-plugin `additionalProperties: false` property schemas), so any
third-party governance plugin — one the plugin registry resolves fine at
runtime — failed strict manifest validation purely because its name wasn't
in this file's hardcoded list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate import validate_file


def _write_agent(tmp_path: Path, governance: list) -> Path:
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "probe"},
        "spec": {"description": "probe agent", "governance": governance},
    }
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return path


def test_third_party_plugin_bare_id_validates(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write_agent(tmp_path, ["some_third_party_governance_plugin"])
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_third_party_plugin_with_arbitrary_config_shape_validates(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = _write_agent(
        tmp_path,
        [{"some_third_party_governance_plugin": {"anything": "goes", "nested": {"a": 1}}}],
    )
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert result.ok, result.issues


def test_still_rejects_non_list_governance(tmp_path: Path) -> None:
    """Shape-only, not typeless: spec.governance still has to be a plugin
    list — some structural validation remains.

    Note: this particular constraint turns out to be enforced by the parent
    agent/overlay schema's own `spec.governance` type declaration, not by
    governance-binding.schema.yaml itself (confirmed by temporarily gutting
    the fragment down to an accept-anything shape — this test still failed
    the manifest, i.e. still passed). Kept here anyway since it's still a
    real, correct claim about the full validation pipeline's behavior; see
    test_still_rejects_wrong_item_type below for a case this fragment
    specifically is responsible for.
    """
    pytest.importorskip("jsonschema")
    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "probe"},
        "spec": {"governance": "not-a-list"},
    }
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok


def test_still_rejects_multi_key_plugin_object(tmp_path: Path) -> None:
    """Each list entry is one plugin: a dict with more than one key is
    still invalid shape, even though the key names themselves are open."""
    pytest.importorskip("jsonschema")
    path = _write_agent(tmp_path, [{"plugin_a": {}, "plugin_b": {}}])
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok


def test_still_rejects_wrong_item_type(tmp_path: Path) -> None:
    """An integer governance list entry must still fail overall validation.

    Checked this one against a deliberately gutted (accept-anything)
    governance-binding.schema.yaml fragment too, the same way I verified
    test_still_rejects_multi_key_plugin_object: turns out THIS rejection
    doesn't come from the fragment's item-level oneOf[string, object]
    either — validate_file's strict path also runs the plain-Python spec
    parser (mas.ctl.manifest.spec_bindings._parse_gov_plugin_list), which
    independently raises "governance list entries must be str or dict, got
    int" regardless of what the JSON schema says. So this test is real and
    correct about end-to-end behavior, but — like
    test_still_rejects_non_list_governance above — it is NOT the fragment
    doing the rejecting. As far as I can tell, only
    test_still_rejects_multi_key_plugin_object exercises a constraint that
    is genuinely this fragment's own (minProperties/maxProperties: 1 has no
    equivalent check in the parser, which just iterates however many keys
    a dict item has)."""
    pytest.importorskip("jsonschema")
    path = _write_agent(tmp_path, [42])
    result = validate_file(path, kind="agent", strict=True, resolve_refs=True)
    assert not result.ok
