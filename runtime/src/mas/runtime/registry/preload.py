#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Import every installed plugin module once, on one thread, before any run starts.

Runs execute on worker threads. Plugin packages import lazily and many of them import their
siblings (a package ``__init__`` that imports its submodules, a layer that imports its client),
so two threads that first touch different modules of such a cluster wait on each other's
per-module import locks and Python raises ``deadlock detected by _ModuleLock``. Importing the
modules here, in a fixed order, on the calling thread, makes every later import a no-op.

All installed plugins are imported, not only those a manifest names: a run also loads plugins
the manifest never mentions (a layer composed in, a tool provider chosen by protocol). Importing
the lot costs about three seconds, once per process. A plugin may also list ``warm`` modules in its
library manifest, the SDK imports it keeps lazy; those are imported too, when installed.
"""

from __future__ import annotations

import importlib
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


def preload_plugins(registry: Any) -> list[str]:
    """Import, in sorted order, the module of every registered plugin whose requirements are present.

    Returns the modules imported by this call; a module already imported is not listed, so a
    second call returns ``[]``. A plugin whose requirements are missing is skipped: it fails
    with its own message when a run uses it. A module that raises is logged and skipped.
    """
    modules: set[str] = set()
    warm: set[str] = set()
    for entry in registry.all_entries():
        for variant in entry.variants.values():
            if variant.module and not variant.missing_requires():
                modules.add(variant.module)
                warm.update(variant.warm)
    imported: list[str] = []
    for module in sorted(modules):
        _import(module, imported)
    for module in sorted(warm):
        _import(module, imported, optional=True)
    return imported


def _import(module: str, imported: list[str], *, optional: bool = False) -> None:
    if module in sys.modules:
        return
    try:
        importlib.import_module(module)
    except MemoryError:
        raise
    except ImportError:
        if not optional:
            logger.warning("preload skipped %s", module, exc_info=True)
        return  # an optional module (an SDK or extra that is not installed) is simply absent
    except Exception:
        logger.warning("preload skipped %s", module, exc_info=True)
        return
    imported.append(module)


def preload_all_plugins() -> list[str]:
    """:func:`preload_plugins` over the process-wide registry."""
    from mas.runtime.registry import get_registry

    return preload_plugins(get_registry())
