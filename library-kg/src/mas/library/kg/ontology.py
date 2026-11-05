#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve ontology TTL paths for KG normalization and validation.

Resolution order for each TTL:
1. Explicit ``config_path`` argument (user override).
2. External ``scope_ontology`` package (SCOPE, canonical source).
3. MAS-Lab extension TTLs under ``library-kg/ontology/extensions/`` for
   classes the native path needs that SCOPE does not yet declare
   (RAGQuery, MemoryCall, SkillCall, governance, …). These are pending
   upstream — they are not a second ontology.

This module never imports a private ontology package.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Local-name stems of extension TTLs shipped with this library. Loaded only
# as a fallback when SCOPE does not already provide the file.
_LOCAL_EXTENSION_TTLS: tuple[str, ...] = (
    "mas-kg-native-extensions",
)


def _get_ontology_path(name: str) -> str:
    """Resolve an ontology TTL path from the SCOPE ``scope_ontology`` package."""
    try:
        from scope_ontology import get_ontology_path
    except ImportError as exc:
        raise ImportError(
            "scope_ontology (ioc-scope-ontology) is required to resolve ontology TTL files"
        ) from exc

    path = get_ontology_path(name)
    return str(path)


def _extensions_dir() -> Path:
    """Return the directory of MAS-Lab extension TTLs.

    Source checkout: ``library-kg/ontology/extensions/``.
    Installed wheel: ``mas/library/kg/ontology_extensions/`` (hatch force-include).
    """
    here = Path(__file__).resolve().parent
    packaged = here / "ontology_extensions"
    if packaged.is_dir():
        return packaged
    # src/mas/library/kg/ontology.py → library-kg/
    repo_ext = here.parents[3] / "ontology" / "extensions"
    return repo_ext


def _resolve_local_extension(stem: str) -> Optional[Path]:
    path = _extensions_dir() / f"{stem}.ttl"
    if path.is_file():
        return path
    logger.warning("MAS-Lab extension TTL %s.ttl not found under %s", stem, _extensions_dir())
    return None


def resolve_mas_ontology_path(config_path: Optional[str] = None) -> Path:
    """Return the path to ``mas-ontology.ttl``.

    Tries: explicit config_path → external SCOPE ``scope_ontology``.
    """
    if config_path:
        path = Path(config_path).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"ontology_path not found: {config_path}")

    return Path(_get_ontology_path("mas-ontology"))


def resolve_mas_shapes_path() -> Optional[Path]:
    """Return the path to ``mas-shapes.ttl`` (generated SHACL class scaffolding)."""
    try:
        return Path(_get_ontology_path("mas-shapes"))
    except FileNotFoundError:
        logger.warning("mas-shapes.ttl not found in scope_ontology")
        return None


def resolve_mas_shapes_custom_path() -> Optional[Path]:
    """Return the path to ``mas-shapes-custom.ttl`` (hand-maintained SHACL rules)."""
    try:
        return Path(_get_ontology_path("mas-shapes-custom"))
    except FileNotFoundError:
        logger.warning("mas-shapes-custom.ttl not found in scope_ontology")
        return None


def resolve_kg_ontology_paths() -> List[Path]:
    """Return TTL paths for the combined KG validation graph.

    Includes ``mas-ontology.ttl`` from SCOPE plus any MAS-Lab
    extension TTLs that declare native-path classes not yet upstream.
    Always resolves — never returns empty (raises if mas-ontology.ttl is
    missing).
    """
    paths: List[Path] = [resolve_mas_ontology_path()]

    for stem in _LOCAL_EXTENSION_TTLS:
        try:
            upstream = Path(_get_ontology_path(stem))
        except (ImportError, FileNotFoundError):
            upstream = None
        if upstream is not None and upstream.is_file():
            continue
        ext = _resolve_local_extension(stem)
        if ext is not None:
            paths.append(ext)

    return paths


def resolve_all_ontology_paths() -> List[Path]:
    """Return bundled SCOPE TTLs plus MAS-Lab native-path extensions."""
    try:
        from scope_ontology import ONTOLOGY_FILES
    except ImportError as exc:
        raise ImportError(
            "scope_ontology (ioc-scope-ontology) is required to resolve ontology TTL files"
        ) from exc

    paths: List[Path] = []
    for name in ONTOLOGY_FILES:
        try:
            paths.append(Path(_get_ontology_path(name)))
        except FileNotFoundError:
            logger.warning("Ontology TTL %s not found in scope_ontology", name)
    for extra in resolve_kg_ontology_paths():
        if extra not in paths:
            paths.append(extra)
    return paths
