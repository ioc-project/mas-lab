#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Trip-planner agents inherit the infra model; they do not pin a hosted id (#191)."""

from __future__ import annotations

from pathlib import Path

import yaml

_AGENTS = Path(__file__).resolve().parents[1] / "apps" / "trip-planner" / "agents"


def test_trip_planner_agents_pin_any_not_a_hosted_model() -> None:
    files = sorted(_AGENTS.rglob("*.yaml"))
    assert files
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        models = ((doc or {}).get("spec") or {}).get("models") or []
        for entry in models:
            assert entry.get("model") == "any", path
