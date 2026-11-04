#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Smoke the samples library package root."""

from __future__ import annotations

import mas.library.samples as samples


def test_package_root_finds_library_yaml() -> None:
    root = samples.package_root()
    assert (root / "library.yaml").is_file()
