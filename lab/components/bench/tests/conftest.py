#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pytest configuration for bench tests."""

from __future__ import annotations

import pytest


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
