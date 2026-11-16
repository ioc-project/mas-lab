#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the MAS_CONTROLLER_MAX_TIMEOUT parser."""

from __future__ import annotations

import pytest
from mas.lab.controller.constants import parse_max_timeout


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(None, 1800, id="unset uses default"),
        pytest.param("  ", 1800, id="blank uses default"),
        pytest.param("300", 300, id="floor is accepted"),
        pytest.param("2000", 2000, id="higher cap is accepted"),
        pytest.param(" 2400 ", 2400, id="surrounding whitespace is ignored"),
    ],
)
def test_parse_max_timeout_accepts(raw, expected):
    assert parse_max_timeout(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
        pytest.param("30m", id="not an integer"),
        pytest.param("299", id="below the fixed request defaults"),
    ],
)
def test_parse_max_timeout_rejects(raw):
    with pytest.raises(RuntimeError, match="MAS_CONTROLLER_MAX_TIMEOUT"):
        parse_max_timeout(raw)
