#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Root test configuration for the knowledge-graph library."""

import sys
from pathlib import Path

# Ensure src/ is on the path for editable-install-free test runs
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
# test-only helpers shared with the other library suites; shipped in no package
_SUPPORT = Path(__file__).resolve().parents[2] / "tests" / "support"
if str(_SUPPORT) not in sys.path:
    sys.path.insert(0, str(_SUPPORT))

import scope_next  # noqa: E402


def pytest_addoption(parser):
    scope_next.add_option(parser)


def pytest_configure(config):
    scope_next.add_marker(config)


def pytest_collection_modifyitems(config, items):
    scope_next.skip_unless_enabled(config, items)
