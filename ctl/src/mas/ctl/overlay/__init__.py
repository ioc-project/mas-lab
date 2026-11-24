#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Overlay merge utilities."""

from mas.ctl.overlay.merge import (
    OverlayTargetError,
    accumulate_agent_patches,
    apply_document_extensions,
    apply_merge_patch,
    extract_agent_overlay_fanout,
    extract_mas_agent_patches,
    fanout_agent_overlay,
    loaded_agent_patches,
    merge_agent_overlay,
    merge_flavour_overlay,
    merge_overlay,
    rekey_agent_patches,
)
from mas.ctl.overlay.normalize import normalize_overlay

__all__ = [
    "OverlayTargetError",
    "accumulate_agent_patches",
    "apply_document_extensions",
    "apply_merge_patch",
    "extract_agent_overlay_fanout",
    "extract_mas_agent_patches",
    "fanout_agent_overlay",
    "loaded_agent_patches",
    "merge_agent_overlay",
    "merge_flavour_overlay",
    "merge_overlay",
    "normalize_overlay",
    "rekey_agent_patches",
]
