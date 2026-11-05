#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""IOA Library — MCP client/server bridge for MAS Lab."""

from __future__ import annotations

from pathlib import Path

# Pin before any a2a import. library_ioa.plugins.a2a is not enough: other
# packages (and GLS) import a2a-sdk first, and the SDK bakes the flag in at
# a2a.utils.telemetry import time. Full rationale: mas.third_party_otel.
import mas.third_party_otel  # noqa: F401


def package_root() -> Path:
    """Return the directory containing ``library.yaml`` for this manifest library."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "library.yaml").is_file():
            return parent
    return here
