#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Overlay governance-list injection — _apply_overlay_governance coverage.

Regression suite: the same block used to be duplicated verbatim in both
load_scenario_config and load_stacked_config, with two bugs neither had a
test for — a same-key overwrite (two overlays both using "sample_governance"
silently dropped the first one's policies once the declarative policy engine
was actually wired up) and a falsy-empty-list check (an explicit
``governance: []`` meant to clear a previous overlay's contribution was
silently ignored, since ``if [] and ...`` never runs).
"""

from __future__ import annotations

from mas.lab.lab.config.scenario_loading import _apply_overlay_governance


def _config(governance: list | None = None) -> dict:
    return {"agents": [{"id": "a1", "governance": governance or []}]}


def test_absent_key_is_a_no_op() -> None:
    config = _config(governance=[{"x": 1}])
    applied = _apply_overlay_governance(None, {}, config)
    assert applied is False
    assert config["agents"][0]["governance"] == [{"x": 1}]


def test_non_empty_governance_concatenates() -> None:
    config = _config(governance=[{"x": 1}])
    applied = _apply_overlay_governance(None, {"governance": [{"y": 2}]}, config)
    assert applied is True
    assert config["agents"][0]["governance"] == [{"x": 1}, {"y": 2}]


def test_explicit_empty_list_clears_regression() -> None:
    """Regression: an explicit governance: [] used to be falsy and skip the
    whole injection block, leaving a previous overlay's governance untouched
    instead of clearing it as the overlay author intended."""
    config = _config(governance=[{"sample_governance": {"policies": [{"name": "old"}]}}])
    applied = _apply_overlay_governance(None, {"governance": []}, config)
    assert applied is True
    assert config["agents"][0]["governance"] == []


def test_overlay_spec_takes_precedence_when_overlay_dict_present() -> None:
    config = _config()
    overlay = {"spec": {"governance": [{"a": 1}]}}
    _apply_overlay_governance(overlay, {"governance": [{"b": 2}]}, config)
    assert config["agents"][0]["governance"] == [{"a": 1}]


def test_falls_back_to_overlay_spec_when_overlay_has_no_governance_key() -> None:
    config = _config()
    overlay = {"spec": {}}
    _apply_overlay_governance(overlay, {"governance": [{"b": 2}]}, config)
    assert config["agents"][0]["governance"] == [{"b": 2}]


def test_no_agents_is_a_no_op() -> None:
    config: dict = {}
    applied = _apply_overlay_governance(None, {"governance": [{"a": 1}]}, config)
    assert applied is False


def test_governance_off_replaces_other_event_plugins() -> None:
    from pathlib import Path

    import yaml
    from mas.lab.lab.config.scenario_loading import _apply_overlay_plugin_list

    overlay = yaml.safe_load(
        (
            Path(__file__).resolve().parents[4] / "labs" / "lifecycle-control.lab" / "overlays" / "governance-off.yaml"
        ).read_text(encoding="utf-8")
    )
    overlay_spec = (overlay.get("spec") or {}).get("patch") or {}
    config = {"agents": [{"id": "desk", "governance": ["sample_governance"], "observability": ["otel"]}]}
    _apply_overlay_governance(overlay, overlay_spec, config)
    _apply_overlay_plugin_list(overlay, overlay_spec, config, "observability")
    assert config["agents"][0]["governance"] == []
    assert config["agents"][0]["observability"] == ["native"]


def test_governance_off_stacked_after_guardrail_clears_policies() -> None:
    from pathlib import Path

    from mas.lab.lab.config.scenario_loading import load_stacked_config

    repo = Path(__file__).resolve().parents[4]
    mas = repo / "library-samples" / "apps" / "trip-planner" / "mas.yaml"
    overlays = repo / "labs" / "lifecycle-control.lab" / "overlays"
    cfg, _ = load_stacked_config(mas, ["with-guardrail", "governance-off"], overlays_dir=overlays, base_dir=overlays)
    assert cfg["agents"]
    for agent in cfg["agents"]:
        assert (agent.get("governance") or []) == [], agent.get("id")
        assert "native" in (agent.get("observability") or []), agent.get("id")


def test_with_guardrail_overlay_lands_on_trip_planner_agents() -> None:
    from pathlib import Path

    from mas.lab.lab.config.scenario_loading import load_stacked_config

    repo = Path(__file__).resolve().parents[4]
    mas = repo / "library-samples" / "apps" / "trip-planner" / "mas.yaml"
    overlays = repo / "labs" / "lifecycle-control.lab" / "overlays"
    cfg, _ = load_stacked_config(mas, ["with-guardrail"], overlays_dir=overlays, base_dir=overlays)
    assert cfg["agents"]
    for agent in cfg["agents"]:
        gov = agent.get("governance") or []
        names = [g if isinstance(g, str) else next(iter(g)) for g in gov]
        assert "sample_governance" in names, agent.get("id")
        obs = [item if isinstance(item, str) else next(iter(item)) for item in (agent.get("observability") or [])]
        assert any(name.rsplit(".", 1)[-1] == "native" for name in obs), agent.get("id")
