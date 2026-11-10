#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shipped Agent and MAS manifests carry no deprecated spec key: governance, control and observability are Controls."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate.deprecations import collect_agent_control_keys_deprecation

REPO = Path(__file__).resolve().parents[1]
_ROOTS = ("docs", "library-samples", "library-standard", "library-ioa", "library-kg", "lab")
_SKIP = ("node_modules", ".venv", "/schemas/", "/.git/")


def _shipped_manifests() -> list[Path]:
    found: list[Path] = []
    for root in _ROOTS:
        base = REPO / root
        if base.is_dir():
            found.extend(path for path in base.rglob("*.yaml") if not any(part in str(path) for part in _SKIP))
    return sorted(found)


def _documents(path: Path) -> list[dict]:
    try:
        return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(doc, dict)]
    except (OSError, yaml.YAMLError):
        return []


def test_no_shipped_agent_or_mas_sets_a_deprecated_control_key() -> None:
    offenders = [
        f"{path.relative_to(REPO)}: {message}"
        for path in _shipped_manifests()
        for doc in _documents(path)
        for _where, message in collect_agent_control_keys_deprecation(doc)
    ]
    assert offenders == []


def test_no_shipped_agent_uses_kind_system() -> None:
    offenders = []
    for path in _shipped_manifests():
        for doc in _documents(path):
            spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
            for item in spec.get("tools") or []:
                if isinstance(item, dict) and item.get("kind") == "system":
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


@pytest.mark.parametrize("key", ["observability", "governance"])
def test_the_collector_flags_each_deprecated_key(key: str) -> None:
    doc = {"kind": "Agent", "metadata": {"name": "a"}, "spec": {key: []}}
    [(where, message)] = collect_agent_control_keys_deprecation(doc)
    assert where == "spec"
    assert f"spec.{key} is deprecated" in message
    assert "still applied" in message
    assert "kind: Control" in message


def test_agent_spec_control_warns_only_for_the_keys_nothing_reads() -> None:
    # retry and circuit_breaker still apply (they move to the infra pipeline next release): no warning yet.
    still_applied = {"kind": "Agent", "spec": {"control": {"retry": {}, "circuit_breaker": {}}}}
    assert collect_agent_control_keys_deprecation(still_applied) == []
    unread = {"kind": "Agent", "spec": {"control": {"budget": {"max_tokens": 1}, "rate_limiter": {}}}}
    [(_, message)] = collect_agent_control_keys_deprecation(unread)
    assert message == "spec.control.budget, spec.control.rate_limiter are deprecated and read by no plugin: remove it"


def test_the_collector_names_every_key_once() -> None:
    doc = {"kind": "MAS", "spec": {"observability": [], "governance": []}}
    [(_, message)] = collect_agent_control_keys_deprecation(doc)
    assert message.startswith("spec.observability, spec.governance are deprecated")


def test_other_kinds_and_clean_specs_are_quiet() -> None:
    assert collect_agent_control_keys_deprecation({"kind": "Flavour", "spec": {"observability": []}}) == []
    assert collect_agent_control_keys_deprecation({"kind": "Agent", "spec": {"tools": []}}) == []


def test_schema_marks_leftover_agent_control_keys_deprecated() -> None:
    from mas.ctl.validate.schemas import load_schema

    props = load_schema("agent")["properties"]["spec"]["properties"]
    for key in ("observability", "governance", "budget", "control"):
        assert props[key]["deprecated"] is True
