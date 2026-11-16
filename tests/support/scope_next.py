#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The ``--scope-next`` switch shared by the library suites that test unreleased SCOPE changes.

Tests marked ``scope_next`` exercise SCOPE ontology and ``norm`` changes that are not released. They
are off; run them against a SCOPE that has the changes with ``pytest --scope-next``. Each suite's
``conftest.py`` calls these three functions from its own hooks, so the option, the marker and the
skip live here once. A process that collects both suites registers the option once.
"""

from __future__ import annotations

import pytest


def add_option(parser: pytest.Parser) -> None:
    try:
        parser.addoption("--scope-next", action="store_true", default=False, help="run the scope_next tests")
    except ValueError:  # the other suite registered it in this process
        pass


def add_marker(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "scope_next: needs unreleased SCOPE changes; run with --scope-next")


def skip_unless_enabled(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--scope-next"):
        return
    skip = pytest.mark.skip(reason="needs unreleased SCOPE changes (run with --scope-next)")
    for item in items:
        if "scope_next" in item.keywords:
            item.add_marker(skip)
