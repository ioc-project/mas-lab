#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-ctl validate rejects undeclared or path-like Agent/MAS identity."""

from __future__ import annotations

from mas.ctl.validate.identity import collect_identity_issues
from mas.ctl.validate import validate_data


def _agent(*, name: str) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": {
            "description": "qa",
            "models": [{"model": "azure/gpt-4o"}],
            "design_pattern": {"type": "react"},
        },
    }


def _mas(*, name: str) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": name},
        "spec": {
            "agency": {"entry": "qa-agent", "agents": [{"id": "qa-agent", "ref": "./agent.yaml"}]},
        },
    }


def test_validate_rejects_empty_agent_name() -> None:
    result = validate_data(_agent(name=""), kind="agent", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("metadata.name" in issue.message for issue in result.issues)


def test_validate_rejects_path_agent_name() -> None:
    result = validate_data(
        _agent(name="/opt/mas-lab/library-samples/apps/trip-planner/agents"),
        kind="agent",
        strict=True,
        resolve_refs=False,
    )
    assert not result.ok
    assert any("filesystem path" in issue.message for issue in result.issues)


def test_validate_rejects_path_mas_name() -> None:
    result = validate_data(
        _mas(name="/opt/apps/trip-planner"),
        kind="mas",
        strict=True,
        resolve_refs=False,
    )
    assert not result.ok
    assert any("filesystem path" in issue.message for issue in result.issues)


def test_validate_accepts_declared_agent_name() -> None:
    result = validate_data(_agent(name="qa-agent"), kind="agent", strict=True, resolve_refs=False)
    assert not any("filesystem path" in issue.message for issue in result.issues)
    assert not any("metadata.name is required" in issue.message for issue in result.issues)


def test_validate_rejects_path_otel_app_name_on_overlay() -> None:
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "observability-otel-json"},
        "spec": {
            "target": {"kind": "Agent"},
            "patch": {
                "observability": [
                    {
                        "otel": {
                            "output_path": "traces/otel_sdk_spans.jsonl",
                            "app_name": "/opt/mas-lab/library-samples/apps/trip-planner/agents",
                        }
                    }
                ]
            },
        },
    }
    result = validate_data(overlay, kind="overlay", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("filesystem path" in issue.message and "app_name" in issue.path for issue in result.issues)


def test_events_to_otel_service_name_is_not_identity() -> None:
    issues = collect_identity_issues(
        {
            "kind": "Pipeline",
            "spec": {
                "steps": [
                    {
                        "type": "events_to_otel",
                        "config": {"service_name": "mas-runtime"},
                    }
                ]
            },
        },
        "pipeline",
    )
    assert any("service_name is not MAS identity" in issue.message for issue in issues)


def test_events_to_otel_app_name_is_enough() -> None:
    issues = collect_identity_issues(
        {
            "kind": "Pipeline",
            "spec": {
                "steps": [
                    {
                        "type": "events_to_otel",
                        "config": {"app_name": "qa-agent"},
                    }
                ]
            },
        },
        "pipeline",
    )
    assert not any("service_name is not MAS identity" in issue.message for issue in issues)
