#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A Flavour's ``bind`` names infra rows; it is deployment, so it stays out of the run hash."""

from __future__ import annotations

import pytest
from mas.lab.benchmark.cache import extract_flavour_info


def test_bind_does_not_change_the_flavour_cache_key() -> None:
    plain = {"observability": ["native"], "agent_comm": {"protocol": "agent-local"}}
    bound = {**plain, "bind": {"agent_comm": ["desk-node"]}}
    assert extract_flavour_info(bound) == extract_flavour_info(plain)


def test_leftover_observability_does_not_change_the_flavour_cache_key() -> None:
    assert extract_flavour_info({"observability": ["native"]}) == extract_flavour_info({"observability": []})
    assert extract_flavour_info({"observability": ["native"]}) == {}


def _control(directory, name: str, plugin: str) -> None:
    (directory / f"{name}.yaml").write_text(
        f"apiVersion: control/v1\nkind: Control\nmetadata: {{name: {name}}}\nspec:\n  governance: [{plugin}]\n",
        encoding="utf-8",
    )


def test_control_changes_the_flavour_cache_key(tmp_path) -> None:
    _control(tmp_path, "sample", "sample_governance")
    _control(tmp_path, "retry", "retry_on_error")
    a = {"control": [{"ref": "sample.yaml"}]}
    b = {"control": [{"ref": "retry.yaml"}]}
    assert extract_flavour_info(a, search_dirs=[tmp_path]) != extract_flavour_info(b, search_dirs=[tmp_path])
    leftover = {"control": {"budget": {}}}
    assert extract_flavour_info(leftover) == {}


def test_cli_control_changes_the_flavour_cache_key(tmp_path) -> None:
    from mas.ctl.session.flavour_control import session_flavour_with_controls

    _control(tmp_path, "sample", "sample_governance")
    _control(tmp_path, "retry", "retry_on_error")
    a = session_flavour_with_controls({}, controls=["sample.yaml"], search_dirs=[tmp_path])
    b = session_flavour_with_controls({}, controls=["retry.yaml"], search_dirs=[tmp_path])
    assert extract_flavour_info(a) != extract_flavour_info(b)
    assert extract_flavour_info({}) != extract_flavour_info(a)


def test_control_agents_selector_changes_the_flavour_cache_key(tmp_path) -> None:
    _control(tmp_path, "gov", "sample_governance")
    entry = {"control": [{"ref": "gov.yaml", "agents": "$entry"}]}
    all_agents = {"control": [{"ref": "gov.yaml", "agents": "$all"}]}
    assert extract_flavour_info(entry, search_dirs=[tmp_path]) != extract_flavour_info(
        all_agents, search_dirs=[tmp_path]
    )


def test_a_control_that_cannot_be_found_is_an_error_not_a_blind_cache_key(tmp_path) -> None:
    from mas.ctl.session.flavour_control import ControlError

    with pytest.raises(ControlError, match="is not a file"):
        extract_flavour_info({"control": [{"ref": "missing.yaml"}]}, search_dirs=[tmp_path])


def test_control_ref_hash_uses_bodies_carried_from_flavour_load(tmp_path, monkeypatch) -> None:
    from mas.lab.flavour.load import load_flavour

    (tmp_path / "gov.yaml").write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: gov}\nspec:\n  governance: [sample_governance]\n",
        encoding="utf-8",
    )
    flavour_path = tmp_path / "desk.yaml"
    flavour_path.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: desk}\n"
        "spec:\n  control:\n    - {ref: gov.yaml, agents: $entry}\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    loaded = load_flavour(flavour_path)
    resolved = extract_flavour_info(loaded)
    assert resolved["spec"]["control"][0]["governance"] == ["sample_governance"]
    assert resolved["spec"]["control"][0]["agents"] == "$entry"
