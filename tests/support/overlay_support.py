#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Test helper: apply one ``-o`` file the way a CLI run does, an Overlay or a Control."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mas.ctl.overlay.merge import merge_overlay
from mas.ctl.session.flavour_control import attach_control_files, is_control_document


def apply_overlay_or_control(
    manifest: dict[str, Any] | None,
    path: Path | str,
    *,
    flavour: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge an Overlay onto the agent. Attach a Control to *flavour*.

    Observability and governance stay on the Control. They are not copied
    onto the agent. Overlay patches that set those keys target ``kind: Control``.
    """
    file = Path(path)
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if is_control_document(data):
        if flavour is None:
            raise TypeError("Control attaches to a Flavour; pass flavour={}")
        attach_control_files(flavour, [file])
        return manifest if isinstance(manifest, dict) else {"spec": {}}
    return merge_overlay(manifest if isinstance(manifest, dict) else {}, data)


def load_agent_and_flavour(
    agent: dict[str, Any] | Path,
    *paths: Path | str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load an Agent, merge Overlay files onto it, attach Control files to a Flavour."""
    if isinstance(agent, dict):
        manifest = agent
    else:
        manifest = yaml.safe_load(Path(agent).read_text(encoding="utf-8")) or {}
    flavour: dict[str, Any] = {}
    for item in paths:
        manifest = apply_overlay_or_control(manifest, item, flavour=flavour)
    return manifest, flavour
