#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Package exports that load on first use, so importing a package does not import everything it names."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any


def lazy_exports(
    package: str, namespace: dict[str, Any], exports: dict[str, str]
) -> tuple[Callable[[str], Any], Callable[[], list[str]]]:
    """Return the ``__getattr__`` and ``__dir__`` of ``package`` for ``exports`` (name -> module that defines it).

    A package ``__init__`` assigns both (``__getattr__, __dir__ = lazy_exports(__name__, globals(), _EXPORTS)``)
    and names the same exports under ``if TYPE_CHECKING:`` for type checkers. The first read of a name imports its
    module and caches the value in the package namespace; any other name raises ``AttributeError``, so
    ``from package import submodule`` still imports the submodule.
    """

    def __getattr__(name: str) -> Any:
        module = exports.get(name)
        if module is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
        namespace[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *exports})

    return __getattr__, __dir__
