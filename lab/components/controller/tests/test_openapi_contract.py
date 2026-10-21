#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Committed OpenAPI spec matches the live controller app."""

from __future__ import annotations

import json
from pathlib import Path

from mas.lab.controller.fastapi_app import create_app

SPEC = Path(__file__).resolve().parents[4] / "docs" / "schemas" / "controller-openapi.json"


def test_openapi_matches_committed_spec():
    app = create_app()
    live = app.openapi()
    # Drop volatile bits that change without a contract change.
    live.pop("servers", None)
    if not SPEC.is_file():
        SPEC.parent.mkdir(parents=True, exist_ok=True)
        SPEC.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
        raise AssertionError(f"wrote initial OpenAPI spec to {SPEC}; re-run the test")
    committed = json.loads(SPEC.read_text(encoding="utf-8"))
    committed.pop("servers", None)
    assert live == committed, (
        "OpenAPI drifted from docs/schemas/controller-openapi.json — "
        "regenerate the file from create_app().openapi()"
    )
