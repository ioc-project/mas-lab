#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""Load flavour YAML as dict for bench cache keys and infra refs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.ctl.session.flavour import warn_deprecated_flavour_keys
from mas.ctl.session.flavour_control import hydrate_flavour_controls
from mas.ctl.validate import validate_data, validation_enabled
from mas.lab.flavour.resolve import bundled_flavour_path, resolve_flavour_path
from mas.runtime.spec.source import load_yaml_mapping


def load_flavour(path: Path | str, *, validate: bool = False, warn_deprecated: bool = False) -> dict[str, Any]:
    """The Flavour document at ``path``.

    ``warn_deprecated`` prints the leftover-key warning (as ``mas-ctl --flavour PATH`` does) for a file the
    user wrote; a bundled Flavour is the default of every run and stays quiet.
    Control refs resolve against this file's directory and the bodies travel with the document.
    """
    file = Path(path)
    data = load_yaml_mapping(file)
    if validation_enabled():
        if validate:
            validate_data(data, kind="flavour").raise_if_failed()
        if warn_deprecated:
            warn_deprecated_flavour_keys(data)
    hydrate_flavour_controls(data, source_dir=file.parent.resolve())
    return data


def load_named_flavour(name: str) -> tuple[dict[str, Any], Path]:
    """The Flavour a run names (a bundled name or a file path) and where it was read.

    A file the user wrote gets the leftover-key warning; a bundled name stays quiet.
    ``FileNotFoundError`` when it is neither.
    """
    path = resolve_flavour_path(name)
    return load_flavour(path, warn_deprecated=bundled_flavour_path(name) is None), path
