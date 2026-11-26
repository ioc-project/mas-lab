#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest configuration for bench tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

pytest_plugins = ["mas.runtime.hostenv_pytest", "lab_secret_state"]

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not read the developer's XDG config or MAS_INFRA_REFS."""
    xdg = tmp_path / "xdg-config"
    (xdg / "mas").mkdir(parents=True, exist_ok=True)
    (xdg / "mas" / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MAS_INFRA_REFS", raising=False)


@pytest.fixture(autouse=True)
def _clear_library_discovery_caches():
    """Reset library discovery and plugin-package claims between tests."""
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


@contextmanager
def loaded_role_plugins_lib() -> Iterator[None]:
    """Register the test library that ships extra generator/scheduler/executor plugins."""
    from mas.runtime.registry import get_registry
    from mas.runtime.registry.bootstrap import register_manifest_data

    lib = _TESTS_DIR / "fixtures" / "role_plugins_lib"
    registry = get_registry()
    snapshot = registry.snapshot()
    data = yaml.safe_load((lib / "library.yaml").read_text(encoding="utf-8"))
    parent = str(lib)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    try:
        register_manifest_data(registry, data, source=lib / "library.yaml")
        yield
    finally:
        registry.restore(snapshot)
        if added and parent in sys.path:
            sys.path.remove(parent)


@pytest.fixture
def role_plugins_lib() -> Iterator[None]:
    with loaded_role_plugins_lib():
        yield
