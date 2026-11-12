#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every reliability component is a pipeline step with an id, so an overlay can A/B it by that id."""

from __future__ import annotations

from typing import Any

from mas.ctl.overlay.merge import merge_overlay

_BASE: dict[str, Any] = {
    "apiVersion": "infra/v1",
    "kind": "InfraBundle",
    "metadata": {"name": "hardened"},
    "spec": {
        "pipeline": [
            {"id": "retry-llm", "layer": "retry", "for": ["llm"], "params": {"max_attempts": 4}},
            {
                "id": "breaker-llm",
                "layer": "circuit_breaker",
                "for": ["llm"],
                "params": {"failure_threshold": 5, "reset_timeout_s": 30},
            },
            {"id": "retry-tool", "layer": "retry", "for": ["tool"], "params": {"max_attempts": 2}},
        ]
    },
}


def _overlay(patch: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "arm-b"},
        "spec": {"target": {"kind": "Infra"}, "patch": patch},
    }


def _steps(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in doc["spec"]["pipeline"]}


def test_an_overlay_tunes_one_step_by_id_and_leaves_the_others() -> None:
    merged = merge_overlay(_BASE, _overlay({"pipeline": [{"id": "retry-llm", "params": {"max_attempts": 1}}]}))
    steps = _steps(merged)
    assert steps["retry-llm"]["params"]["max_attempts"] == 1
    assert steps["retry-llm"]["layer"] == "retry"  # the rest of the step is kept
    assert steps["breaker-llm"]["params"]["failure_threshold"] == 5
    assert steps["retry-tool"]["params"]["max_attempts"] == 2


def test_an_overlay_switches_one_component_off_without_removing_it() -> None:
    merged = merge_overlay(_BASE, _overlay({"pipeline": [{"id": "breaker-llm", "enabled": False}]}))
    steps = _steps(merged)
    assert steps["breaker-llm"]["enabled"] is False
    assert steps["breaker-llm"]["params"]["reset_timeout_s"] == 30  # its settings stay for arm A
    assert steps["retry-llm"].get("enabled") is not False


def test_a_step_without_an_id_is_still_addressed_by_its_layer() -> None:
    base = {
        **_BASE,
        "spec": {"pipeline": [{"layer": "circuit_breaker", "for": ["llm"], "params": {"failure_threshold": 5}}]},
    }
    merged = merge_overlay(
        base, _overlay({"pipeline": [{"layer": "circuit_breaker", "params": {"failure_threshold": 2}}]})
    )
    [step] = merged["spec"]["pipeline"]
    assert step["params"]["failure_threshold"] == 2 and step["for"] == ["llm"]


def test_every_shipped_infra_ref_resolves_and_the_reliability_ones_list_their_steps() -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.main import app

    listed = CliRunner().invoke(app, ["infra", "list"]).output.split("\n")
    refs = [line.split()[0] for line in listed if line.strip()]
    assert {"standard:retry-llm", "standard:retry-tool", "standard:circuit-breaker"} <= set(refs)
    for ref in refs:
        result = CliRunner().invoke(app, ["infra", "show", ref])
        assert result.exit_code == 0, f"{ref}: {result.output}"
    shown = CliRunner().invoke(app, ["infra", "show", "standard:llm-proxy-hardened", "--pipeline"]).output
    assert "retry  for=llm" in shown and "id=retry-llm" in shown
    assert "circuit_breaker  for=llm" in shown and "id=breaker-llm" in shown and "on=unavailable" in shown
