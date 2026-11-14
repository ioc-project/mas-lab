#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest configuration for runtime tests."""

from __future__ import annotations

import os

import pytest

# pytest runtime/tests does not load repo-root tests/conftest.py.
os.environ.pop("MAS_LIBRARY_PATHS", None)
os.environ.pop("MAS_WORKSPACE_ROOT", None)


@pytest.fixture(autouse=True)
def _clear_library_discovery_caches():
    """Reset the lru_cache-memoized library/catalog discovery helpers.

    ``mas.library_roots``/``mas.library_catalog`` memoize ``library.yaml``
    parses and app/dataset indices keyed by resolved root path (see
    ``clear_library_discovery_cache`` / ``clear_catalog_discovery_cache``).
    Different tests normally use distinct ``tmp_path`` roots so this would
    not matter, but a test that writes a library dir, resolves it, then
    rewrites the same path and resolves again must still see the change —
    clear before and after every test so no run starts or ends with a
    stale entry.
    """
    from mas.library_catalog import clear_catalog_discovery_cache
    from mas.library_roots import clear_library_discovery_cache, unregister_lab_dirs
    from mas.runtime.registry.bootstrap import clear_library_plugin_registration

    clear_catalog_discovery_cache()
    clear_library_discovery_cache()
    unregister_lab_dirs()
    clear_library_plugin_registration()
    yield
    clear_catalog_discovery_cache()
    clear_library_discovery_cache()
    unregister_lab_dirs()
    clear_library_plugin_registration()


@pytest.fixture(autouse=True)
def _isolate_workspace_root(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep runtime tests off the developer's workspace and sibling worktrees.

    ``pytest runtime/tests`` does not load repo-root ``tests/conftest.py``,
    so without this the suite reads ``~/.config/mas`` and ``MAS_LIBRARY_PATHS``.
    """
    monkeypatch.delenv("MAS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("MAS_LIBRARY_PATHS", raising=False)
    xdg = tmp_path_factory.mktemp("xdg-runtime")
    for name in ("config", "data", "cache", "state"):
        (xdg / name).mkdir()
    (xdg / "config" / "mas").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg / "state"))


@pytest.fixture
def require_samples_library() -> None:
    """Skip when the ``samples`` manifest library entry point is not installed."""
    from mas.runtime.package_refs import resolve_library_scheme_root

    if resolve_library_scheme_root("samples") is None:
        pytest.skip("samples library not installed")
