#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest plugin: tests see ``os.environ`` (so ``monkeypatch.setenv`` works) as the host environment."""

from __future__ import annotations

import os

import pytest
from mas.runtime import hostenv


def pytest_configure(config: pytest.Config) -> None:
    """Session fixtures (a controller daemon, say) run before any test fixture: install here as well."""
    hostenv.install(os.environ)


@pytest.fixture(autouse=True)
def _host_environment():
    hostenv.install(os.environ)
    yield
    hostenv.install(os.environ)
