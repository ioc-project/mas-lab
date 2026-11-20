#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bench runner routing helpers — manifest kind detection for MasBenchRunner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAS_KINDS = ("mas", "app", "workflow")
_MAS_FILE_NAMES = frozenset({"mas.yaml", "mas.yml", "mas.json"})


class UnrecognisedManifestError(ValueError):
    """The manifest the user named has no kind this runner can run."""


def manifest_kind(config: dict[str, Any]) -> str:
    """The lower-cased ``kind`` of a loaded manifest, ``""`` when it has none."""
    return str(config.get("kind", "")).strip().lower()


def is_oasf_document(config: dict[str, Any]) -> bool:
    """An OASF agent record: no ``kind``, but ``metadata`` and ``spec`` mappings."""
    return isinstance(config.get("metadata"), dict) and isinstance(config.get("spec"), dict)


def _named_mas(spec_path: Path) -> bool:
    return spec_path.name.casefold() in _MAS_FILE_NAMES


def _sibling_mas(spec_path: Path) -> Path | None:
    """A neighbouring ``mas.yaml`` / ``mas.yml`` that is not the named file itself."""
    named = spec_path.resolve()
    try:
        candidates = sorted(spec_path.parent.iterdir())
    except OSError:
        return None
    found: dict[str, Path] = {}
    for candidate in candidates:
        key = candidate.name.casefold()
        if key in {"mas.yaml", "mas.yml"} and candidate.is_file() and candidate.resolve() != named:
            found.setdefault(key, candidate)
    return found.get("mas.yaml") or found.get("mas.yml")


def is_mas_manifest_kind(config: dict[str, Any], spec_path: Path) -> bool:
    """True when *config* / *spec_path* denote a MAS (not a standalone agent) manifest.

    Only the named file decides: its ``kind``, or the exact file name ``mas.yaml`` /
    ``mas.yml`` / ``mas.json``. A sibling file never makes another file a MAS.
    """
    kind = manifest_kind(config)
    if kind == "agent":
        return False
    return kind in MAS_KINDS or _named_mas(spec_path)


def mas_manifest_path(config: dict[str, Any], spec_path: Path) -> Path | None:
    """Return the manifest that runs: the file named, or ``None`` for a standalone agent.

    A standalone agent is ``kind: agent``, an OASF record, or a file with no ``kind`` and no
    ``mas.yaml`` / ``mas.yml`` beside it. A ``kind`` that is not recognised (a typo), or a file
    with no ``kind`` next to a ``mas.yaml``, is refused with :class:`UnrecognisedManifestError`:
    the sibling is never substituted and a misspelt ``kind`` never runs as an agent.
    """
    if is_mas_manifest_kind(config, spec_path):
        return spec_path
    kind = manifest_kind(config)
    if kind == "agent":
        return None
    sibling = _sibling_mas(spec_path)
    named_like_mas = spec_path.name.casefold().startswith("mas.")
    if not kind and not named_like_mas and (sibling is None or is_oasf_document(config)):
        return None  # a bare agent file: legacy standalone agents carry no ``kind``
    message = (
        f"{spec_path}: kind {kind or '(missing)'!r} is not recognised; accepted kinds are "
        f"{', '.join(MAS_KINDS)} (or agent for a single agent), or name the file mas.yaml."
    )
    if sibling is not None:
        message += f" The sibling {sibling.name} is NOT used."
    raise UnrecognisedManifestError(message)
