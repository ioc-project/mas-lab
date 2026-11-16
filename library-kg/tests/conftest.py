#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Root test configuration for the knowledge-graph library."""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on the path for editable-install-free test runs
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# Tests marked ``scope_next`` exercise SCOPE ontology and ``norm`` changes that are not released.
# They are off; run them against a SCOPE that has the changes with ``pytest --scope-next``.
def pytest_addoption(parser):
    parser.addoption(
        "--scope-next", action="store_true", default=False, help="run the scope_next tests"
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "scope_next: needs unreleased SCOPE changes; run with --scope-next"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--scope-next"):
        return
    skip = pytest.mark.skip(reason="needs unreleased SCOPE changes (run with --scope-next)")
    for item in items:
        if "scope_next" in item.keywords:
            item.add_marker(skip)
