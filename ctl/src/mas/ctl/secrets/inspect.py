#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What the file store holds: names and keys, never a value."""

from __future__ import annotations

import re
from pathlib import Path

from mas.runtime.secrets import SecretRef
from mas.runtime.secrets.ref import NAME_PATTERN

_NAME_RE = re.compile(NAME_PATTERN)


def list_secret_refs(directory: Path) -> list[SecretRef]:
    """Every ``<name>`` file (key ``value``) and ``<name>/<key>`` file. Dotted names are skipped."""
    if not directory.is_dir():
        return []
    refs: list[SecretRef] = []
    for child in sorted(directory.iterdir()):
        if not _NAME_RE.match(child.name):
            continue
        if child.is_dir() and not child.is_symlink():
            for leaf in sorted(child.iterdir()):
                if _NAME_RE.match(leaf.name):
                    refs.append(SecretRef(name=child.name, key=leaf.name))
        else:
            refs.append(SecretRef(name=child.name))
    return refs
