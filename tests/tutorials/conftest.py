#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fixtures for tutorial integration tests. Helpers live in ``tutorial_support``.

Session-scoped daemon from tests/conftest.py (autouse) — mas-lab CLI subprocesses
auto-start the daemon and register client sessions per process.
"""

from __future__ import annotations

import pytest
from tutorial_support import T01, T02, T03


@pytest.fixture
def t01_dir():
    return T01


@pytest.fixture
def t02_dir():
    return T02


@pytest.fixture
def t03_dir():
    return T03
