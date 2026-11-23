#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest plugin: the host environment and the secret store a test sees, reset around every test.

* Tests see ``os.environ`` (so ``monkeypatch.setenv`` works) as the host environment.
* Every test runs with a store holding ``secret`` for each LLM provider default name (``slim`` a
  SLIM-sized value; not ``neo4j``, ``clickhouse``, ``hf`` or ``ui-ingest``); a test marked
  ``no_secrets`` runs with no store. The process resolver and the overlay a test leaves
  behind are put back as they were, so tests do not depend on the order they run in.
"""

from __future__ import annotations

import os

import pytest
from mas.runtime import hostenv


def pytest_configure(config: pytest.Config) -> None:
    """Session fixtures (a controller daemon, say) run before any test fixture: install here as well."""
    config.addinivalue_line("markers", "no_secrets: run without the test secret store")
    hostenv.install(os.environ)


@pytest.fixture(autouse=True)
def _host_environment():
    hostenv.install(os.environ)
    hostenv.clear_overlay(tuple(hostenv.overlay_snapshot()))
    yield
    hostenv.clear_overlay(tuple(hostenv.overlay_snapshot()))
    hostenv.install(os.environ)


class _DefaultStore:
    def get(self, ref):
        from mas.runtime.secrets.env_names import KNOWN_ENV_NAMES
        from mas.runtime.secrets.errors import SecretMissing
        from mas.runtime.secrets.value import SecretValue

        slim = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
        if ref.name == "slim":
            return SecretValue(slim.encode(), name=ref.name)
        if ref.name in set(KNOWN_ENV_NAMES.values()) - {"neo4j", "clickhouse"}:
            return SecretValue(b"secret", name=ref.name)
        raise SecretMissing(ref)

    def describe(self, ref):
        from mas.runtime.secrets.errors import SecretMissing
        from mas.runtime.secrets.store import SecretInfo

        try:
            return SecretInfo(present=True, size=len(self.get(ref).reveal()))
        except SecretMissing:
            return SecretInfo(present=False)

    def change_token(self, ref):
        return 0


@pytest.fixture(autouse=True)
def _test_secrets(request: pytest.FixtureRequest):
    from mas.runtime.secrets.resolver import _PROCESS, NullResolver, SecretResolver

    saved = list(_PROCESS)
    no_store = request.node.get_closest_marker("no_secrets") is not None
    _PROCESS[:] = [NullResolver() if no_store else SecretResolver(_DefaultStore())]
    yield
    _PROCESS[:] = saved
