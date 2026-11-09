#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for observability format derivation from manifest plugins."""

from __future__ import annotations

import pytest
from mas.ctl.session.manifest_config import (
    agent_id_from_manifest,
    derive_observability_format,
    mas_id_from_manifest,
    observability_config_from_manifest,
    require_agent_id_from_manifest,
    resolve_mas_id,
)


def test_derive_format_native_only() -> None:
    assert derive_observability_format(["native"]) == "native"


def test_derive_format_otel_only() -> None:
    assert derive_observability_format(["otel"]) == "otel"


def test_derive_format_both_plugins() -> None:
    assert derive_observability_format(["native", "otel"]) == "both"


def test_derive_format_cli_override() -> None:
    assert derive_observability_format(["native"], cli_override="otel") == "otel"


class TestLeftoverFlavourObservabilityIsUnread:
    def test_flavour_observability_does_not_supply_plugins(self) -> None:
        cfg = observability_config_from_manifest(
            {"spec": {}},
            flavour_spec={"observability": ["otel"]},
        )
        assert cfg.plugins == ["native"]
        assert cfg.enabled is True

    def test_leftover_agent_observability_still_applies(self) -> None:
        cfg = observability_config_from_manifest(
            {"spec": {"observability": ["otel"]}},
            flavour_spec={"observability": ["native"]},
        )
        assert cfg.plugins == ["otel"]

    def test_no_flavour_no_manifest_records_native(self) -> None:
        cfg = observability_config_from_manifest({"spec": {}}, flavour_spec=None)
        assert cfg.plugins == ["native"]
        assert cfg.enabled is True

    def test_cli_events_file_is_the_shortcut_path(self) -> None:
        cfg = observability_config_from_manifest(
            {"spec": {}},
            flavour_spec={"observability": [{"native": {"path": "traces/from-flavour.jsonl"}}]},
            cli_events_file="traces/from-cli.jsonl",
        )
        assert cfg.events_file == "traces/from-cli.jsonl"
        assert cfg.plugins == ["native"]


def test_resolve_mas_id_from_mas_manifest() -> None:
    assert resolve_mas_id({"kind": "MAS", "metadata": {"name": "trip-planner"}}) == "trip-planner"


def test_mas_id_from_loaded_agent_document() -> None:
    assert mas_id_from_manifest({"kind": "Agent", "metadata": {"name": "qa-agent"}}) == "qa-agent"
    assert agent_id_from_manifest({"kind": "Agent", "metadata": {"name": "qa-agent"}}) == "qa-agent"


def test_mas_id_ignores_non_schema_fields() -> None:
    assert (
        mas_id_from_manifest(
            {
                "kind": "Agent",
                "metadata": {"name": "moderator", "mas_id": "trip-planner"},
                "mas": {"name": "trip-planner"},
            }
        )
        == "moderator"
    )


def test_resolve_mas_id_explicit_overrides_document() -> None:
    assert (
        resolve_mas_id(
            {"kind": "MAS", "metadata": {"name": "trip-planner"}},
            mas_id="override",
        )
        == "override"
    )


def test_require_agent_id_from_manifest() -> None:
    assert require_agent_id_from_manifest({"kind": "Agent", "metadata": {"name": "qa-agent"}}) == "qa-agent"
    with pytest.raises(ValueError, match="metadata.name is required"):
        require_agent_id_from_manifest({"kind": "Agent", "metadata": {}})
    with pytest.raises(ValueError, match="metadata.name is required"):
        require_agent_id_from_manifest({"kind": "MAS", "spec": {}})
    with pytest.raises(ValueError, match="filesystem path"):
        require_agent_id_from_manifest({"kind": "Agent", "metadata": {"name": "/opt/apps/trip-planner"}})
    assert require_agent_id_from_manifest({"kind": "Overlay"}) == ""
