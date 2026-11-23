#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Import everything a run reaches lazily, once, on one thread, before run threads start.

The plugins are imported by :mod:`mas.runtime.registry.preload`. The runtime and ``mas.ctl`` packages
are not plugins, yet run threads import their modules lazily (compose, session bootstrap, the
benchmark runner, observability factories), and those modules import each other in cycles: two threads
that first enter at different modules wait on each other's import locks (``deadlock detected by
_ModuleLock``). Importing both trees here makes every later import a no-op.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys

from mas.runtime.registry.preload import preload_all_plugins

logger = logging.getLogger(__name__)

_RUN_PATH_PACKAGES = ("mas.runtime", "mas.ctl")


def preload_package_tree(name: str) -> list[str]:
    """Import every module under package *name*, sorted by walk order; return those imported by this call."""
    package = importlib.import_module(name)
    imported: list[str] = []
    skipped: list[str] = []
    for info in pkgutil.walk_packages(package.__path__, f"{name}.", onerror=skipped.append):
        if info.name in sys.modules:
            continue
        try:
            importlib.import_module(info.name)
        except MemoryError:
            raise
        except Exception:
            logger.warning("preload skipped %s", info.name, exc_info=True)
            continue
        imported.append(info.name)
    for failed in skipped:
        logger.warning("preload could not walk %s", failed)
    return imported


def preload_run_path() -> list[str]:
    """Plugins, then the runtime and ctl packages; the modules imported by this call."""
    imported = preload_all_plugins()
    for package in _RUN_PATH_PACKAGES:
        imported.extend(preload_package_tree(package))
    return imported
