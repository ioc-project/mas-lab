#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Writable controller workspace: the place new apps and datasets land."""

from __future__ import annotations

from pathlib import Path

from mas.lab.controller.kinds import LIBRARY_BASE_KINDS
from mas.lab.controller.scope_catalog import Scope
from mas.runtime.constants import LIBRARY_MANIFEST_FILENAME

WORKSPACE_SLUG = "workspace"
WORKSPACE_SCOPE_ID = f"library:{WORKSPACE_SLUG}"

_MANIFEST = """\
apiVersion: mas/v1
kind: Library
metadata:
  name: workspace
  description: Controller workspace for new applications, overlays, and datasets
"""

_root: Path | None = None


def init_workspace_library(parent: Path) -> Path:
    """Create ``parent/library`` if needed and remember it for the catalog.

    *parent* is the project workspace (``MAS_WORKSPACE_ROOT`` /
    ``mas-lab serve --workspace``) when one is pinned, otherwise the
    controller ``--state-dir``.
    """
    global _root
    root = Path(parent) / "library"
    root.mkdir(parents=True, exist_ok=True)
    (root / "apps").mkdir(exist_ok=True)
    (root / "overlays").mkdir(exist_ok=True)
    (root / "datasets").mkdir(exist_ok=True)
    manifest = root / LIBRARY_MANIFEST_FILENAME
    if not manifest.is_file():
        manifest.write_text(_MANIFEST, encoding="utf-8")
    _root = root
    return root


def reset_workspace_library() -> None:
    """Clear the process-wide workspace pin (tests / apps without ``state_dir``)."""
    global _root
    _root = None


def workspace_root() -> Path | None:
    return _root


def workspace_scope() -> Scope | None:
    if _root is None:
        return None
    return Scope(
        id=WORKSPACE_SCOPE_ID,
        kind="library",
        name=WORKSPACE_SLUG,
        title="Workspace",
        description="Controller workspace for new applications, overlays, and datasets",
        origin="workspace",
        writable=True,
        root=_root.resolve(),
        kinds=list(LIBRARY_BASE_KINDS),
    )
