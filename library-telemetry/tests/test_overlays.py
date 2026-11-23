#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every shipped observability overlay names a usable OTLP endpoint, never a variable name."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

OVERLAYS = sorted((Path(__file__).resolve().parents[1] / "overlays").glob("*.yaml"))


def _otel_entries(doc: dict) -> list[dict]:
    entries = (doc.get("spec") or {}).get("observability") or []
    return [entry["otel"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("otel"), dict)]


@pytest.mark.parametrize("path", OVERLAYS, ids=lambda p: p.name)
def test_an_otlp_endpoint_is_an_http_url(path: Path) -> None:
    # kills: an overlay that exports to the literal text of an environment variable name
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for otel in _otel_entries(doc):
        if "otlp_endpoint" not in otel:
            continue
        parts = urlsplit(str(otel["otlp_endpoint"]))
        assert parts.scheme in {"http", "https"} and parts.hostname, otel["otlp_endpoint"]


def test_the_collector_overlays_export_over_otlp() -> None:
    names = {path.name for path in OVERLAYS}
    assert {"observability-otel-collector.yaml", "observability-otel-realtime.yaml"} <= names
    for name in ("observability-otel-collector.yaml", "observability-otel-realtime.yaml"):
        doc = yaml.safe_load((OVERLAYS[0].parent / name).read_text(encoding="utf-8"))
        assert [otel.get("otlp_endpoint") for otel in _otel_entries(doc)] == ["http://localhost:4318"]
