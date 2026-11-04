#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS Library Standard — infra bundles, tools, and plugin URNs."""

from __future__ import annotations

from pathlib import Path

from mas._dist import installed_version


def package_root() -> Path:
    """Return the directory containing ``library.yaml`` for this manifest library."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "library.yaml").is_file():
            return parent
    return here


__version__ = installed_version(file=__file__)
