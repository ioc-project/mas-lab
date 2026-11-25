#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fixtures for tutorial integration tests. Helpers live in ``tutorial_support``.

Session-scoped daemon from tests/conftest.py (autouse) — mas-lab CLI subprocesses
auto-start the daemon and register client sessions per process.
"""

from __future__ import annotations

import fcntl
import tempfile
from pathlib import Path

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


# Tests that start a server on a port written in the tutorial's manifests (the SLIM node of tutorial 07, the A2A server
# of tutorial 05) cannot run beside each other, so under ``pytest -n`` each set holds a file lock for the length of
# the test. (``--dist loadgroup`` would keep a set on one worker, but pytest-xdist 3.8 does not see the group name
# with pytest 9: it appends it to ``item._nodeid``, which pytest no longer reads.)
_PORT_GROUPS = (
    ("tuto-07", "test_tutorial_07", "tutorial-07-ports"),
    ("tuto-05", "test_tutorial_05", "tutorial-05-ports"),
)


def _port_group(node) -> str | None:
    for scenario, module, group in _PORT_GROUPS:
        if node.module.__name__.endswith(module) or (scenario in node.nodeid and "scenario_commands" in node.nodeid):
            return group
    return None


@pytest.fixture(autouse=True)
def _port_group_lock(request):
    group = _port_group(request.node)
    if group is None:
        yield
        return
    with open(Path(tempfile.gettempdir()) / f"mas-lab-{group}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
