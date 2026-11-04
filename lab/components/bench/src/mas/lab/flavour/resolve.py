#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Resolve runtime flavours exclusively from ``mas.library.standard``."""


import contextlib  # noqa: E402
import importlib.resources  # noqa: E402
from pathlib import Path  # noqa: E402

_FLAVOUR_PACKAGE = "mas.library.standard"


def bundled_flavour_path(name: str) -> Path | None:
    """Return path to ``flavours/<name>.yaml`` inside ``mas.library.standard``."""
    bare = name[4:] if name.startswith("lib:") else name
    filename = f"{bare}.yaml"
    try:
        resource = importlib.resources.files(_FLAVOUR_PACKAGE).joinpath("flavours", filename)
        with contextlib.ExitStack() as stack:
            path = stack.enter_context(importlib.resources.as_file(resource))
            if path.exists():
                return path
    except Exception:
        return None
    return None


def resolve_flavour_path(name: str) -> Path:
    """Resolve a flavour name to the bundled library-standard file.

    *name* may also be an absolute or relative path to a ``.yaml`` / ``.yml``
    flavour manifest (e.g. a lab-local ``flavours/outshift.yaml``).

    Raises FileNotFoundError when the flavour is not shipped in library-standard.
    Experiment and lab directories must not ship duplicate flavour YAML.
    """
    path_candidate = Path(name).expanduser()
    if path_candidate.suffix in (".yaml", ".yml") and path_candidate.is_file():
        return path_candidate.resolve()

    path = bundled_flavour_path(name)
    if path is None:
        bare = name[4:] if name.startswith("lib:") else name
        raise FileNotFoundError(
            f"Flavour {bare!r} not found in {_FLAVOUR_PACKAGE}/flavours/ "
            f"(add it there; do not copy flavours into labs or experiments)"
        )
    return path
