#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load kind: Control documents and attach them from a Flavour's spec.control."""

from mas.ctl.session.flavour_control.attach import (
    apply_control_overlays,
    apply_controls,
    apply_flavour_controls,
    apply_overlay_controls_to_manifest,
    apply_resolved_controls,
    attach_cli_controls,
    attach_control_files,
    attach_controls,
    attach_overlay_controls,
    session_controls_from_overlay_paths,
    session_flavour_with_controls,
)
from mas.ctl.session.flavour_control.documents import (
    control_body_for_hash,
    control_files_from_paths,
    control_row_from_path,
    flavour_control_rows,
    hydrate_flavour_controls,
    is_control_document,
    load_control,
    resolve_controls,
    unique_controls,
)
from mas.ctl.session.flavour_control.error import ControlError
from mas.ctl.session.flavour_control.plugins import (
    control_lists_for_agent,
    matching_controls,
    plugin_entry,
)
from mas.ctl.session.flavour_control.refs import absolute_path_findings, control_search_dirs
from mas.ctl.session.flavour_control.selectors import agent_matches, parse_control_option

__all__ = [
    "ControlError",
    "absolute_path_findings",
    "agent_matches",
    "apply_control_overlays",
    "apply_controls",
    "apply_flavour_controls",
    "control_lists_for_agent",
    "apply_overlay_controls_to_manifest",
    "apply_resolved_controls",
    "attach_cli_controls",
    "attach_control_files",
    "attach_controls",
    "attach_overlay_controls",
    "control_body_for_hash",
    "control_files_from_paths",
    "control_row_from_path",
    "control_search_dirs",
    "flavour_control_rows",
    "hydrate_flavour_controls",
    "is_control_document",
    "plugin_entry",
    "load_control",
    "matching_controls",
    "parse_control_option",
    "resolve_controls",
    "session_controls_from_overlay_paths",
    "session_flavour_with_controls",
    "unique_controls",
]
