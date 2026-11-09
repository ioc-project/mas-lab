#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest configuration for ctl tests."""

from pathlib import Path

import pytest

collect_ignore = ["pending_port"]


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not read the developer's XDG config or MAS_INFRA_REFS."""
    xdg = tmp_path / "xdg-config"
    (xdg / "mas").mkdir(parents=True, exist_ok=True)
    (xdg / "mas" / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MAS_INFRA_REFS", raising=False)


@pytest.fixture(autouse=True)
def _fresh_notices() -> None:
    """Each test sees the warnings it causes, not the ones an earlier test already printed."""
    from mas.ctl.session.notices import reset_notices

    reset_notices()


@pytest.fixture(autouse=True)
def _no_slim_settle_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake node needs no time to propagate subscriptions."""
    try:
        from library_ioa.plugins.slim.actor import ConnectionActor
    except ImportError:  # library-ioa is not installed: no SLIM node to wait for
        return

    monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
    monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)


@pytest.fixture(autouse=True)
def _clear_library_discovery_caches():
    """Reset the lru_cache-memoized library/catalog discovery helpers.

    See ``runtime/tests/conftest.py`` for the full rationale: these caches
    are keyed by resolved root path, so a test that writes a library dir,
    resolves it, then rewrites the same path must still see the change.
    """
    from mas.library_catalog import clear_catalog_discovery_cache
    from mas.library_roots import clear_library_discovery_cache

    clear_catalog_discovery_cache()
    clear_library_discovery_cache()
    yield
    clear_catalog_discovery_cache()
    clear_library_discovery_cache()


class _PathProbe:
    """A governance plugin that takes a file path, to test how a Control's paths are read."""

    def __init__(self, script_file: str = "", **_: object) -> None:
        self.script_file = script_file

    def evaluate_egress(self, intent, *, config):
        from mas.runtime.kernel.coupling import GovDecision

        return GovDecision.ALLOW, "path_probe", "probe"


@pytest.fixture(scope="session", autouse=True)
def _path_probe_plugin():
    from mas.runtime.registry import register_plugin, unregister_plugin

    urn = "mas.gov.test_path_probe"
    register_plugin(urn, _PathProbe, shortcuts=["path_probe"], attributes={"plugin_type": "governance"})
    yield
    unregister_plugin(urn)
