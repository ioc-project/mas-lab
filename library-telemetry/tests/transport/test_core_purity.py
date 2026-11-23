#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generic transport core contains no protocol names."""

from __future__ import annotations

from pathlib import Path

FORBIDDEN = ("a2a", "mcp", "slim")

ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_TRANSPORT = ROOT / "src" / "mas" / "library" / "telemetry" / "transport"
RUNTIME_TRANSPORT = Path(__file__).resolve().parents[3] / "runtime" / "src" / "mas" / "runtime" / "transport"
RUNTIME_SCHEMA = Path(__file__).resolve().parents[3] / "runtime" / "src" / "mas" / "runtime" / "schema" / "transport.py"


def _files() -> list[Path]:
    files = [RUNTIME_SCHEMA]
    files.extend(sorted(TELEMETRY_TRANSPORT.glob("*.py")))
    files.extend(sorted(RUNTIME_TRANSPORT.glob("*.py")))
    return files


def test_core_files_contain_no_protocol_names() -> None:
    hits: list[str] = []
    for path in _files():
        text = path.read_text(encoding="utf-8").lower()
        for name in FORBIDDEN:
            if name in text:
                hits.append(f"{path}: {name}")
    assert hits == []
