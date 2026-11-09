#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Attach Control rows to a Flavour. Control lists stay on the Control."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mas.ctl.infra.bind import flavour_spec_of
from mas.ctl.session.flavour_control.documents import (
    control_files_from_paths,
    control_row,
    is_control_document,
    resolve_controls,
    row_from_data,
    unique_controls,
)
from mas.ctl.session.flavour_control.error import ControlError
from mas.ctl.session.flavour_control.plugins import (
    duplicate_plugin_id_findings,
    matching_controls,
    priority_findings,
)
from mas.ctl.session.flavour_control.refs import as_path, control_search_dirs, read_mapping
from mas.ctl.session.flavour_control.selectors import parse_control_option
from mas.ctl.session.flavour_control.spec import leftover_pipeline_error, leftover_spec_key
from mas.ctl.session.notices import warn_once


def _refuse_leftover_control_keys(controls: Sequence[dict[str, Any]]) -> None:
    for control in controls:
        body = control.get("spec") or {}
        label = str(control.get("name") or control.get("ref") or "Control")
        leftover = leftover_spec_key(body)
        if leftover:
            raise ControlError(leftover_pipeline_error(label, leftover))


def apply_controls(
    manifest: dict[str, Any] | None,
    controls: Sequence[dict[str, Any]],
    *,
    agent_id: str,
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Matching Controls stay on the Flavour. The agent spec is not rewritten.

    Observability and governance are Control attributes.
    """
    _refuse_leftover_control_keys(controls)
    matching_controls(controls, agent_id=agent_id, entry_id=entry_id, delegate_ids=delegate_ids)
    merged = copy.deepcopy(manifest) if isinstance(manifest, dict) else {"spec": {}}
    return merged, []


def attach_controls(flavour: Any, extra: Sequence[dict[str, Any]]) -> None:
    """Add resolved Control rows to a session Flavour's loaded ``_controls``.

    The command line and ``-o`` add rows here, after the Flavour loaded; the Flavour file's own
    ``spec.control`` list is not rewritten, so it never holds a path from the caller's machine.
    """
    if not extra:
        return
    spec = flavour_spec_of(flavour)
    if not isinstance(spec, dict) or not isinstance(flavour, dict):
        raise ControlError("Controls attach to a Flavour spec mapping")
    carried = list(spec.get("_controls") or []) if isinstance(spec.get("_controls"), list) else []
    spec["_controls"] = unique_controls([*carried, *[copy.deepcopy(item) for item in extra]])


def cli_control_rows(specs: Sequence[str], *, search_dirs: Sequence[Path | str]) -> list[dict[str, Any]]:
    """Resolve ``--control REF[@SELECTOR]`` values. A relative REF is looked up in ``search_dirs``, never guessed."""
    dirs = control_search_dirs(*search_dirs)
    rows: list[dict[str, Any]] = []
    for text in specs:
        ref, agents = parse_control_option(text)
        rows.append(control_row({"ref": ref, "agents": agents}, dirs, from_cli=True))
    return rows


def attach_cli_controls(flavour: Any, specs: Sequence[str], *, search_dirs: Sequence[Path | str]) -> None:
    """Attach ``--control`` values to the session Flavour, the same way a ``spec.control`` row attaches."""
    attach_controls(flavour, cli_control_rows(specs, search_dirs=search_dirs))


def attach_control_files(flavour: Any, paths: Sequence[Path | str]) -> None:
    """Attach ``kind: Control`` files to the session Flavour, the same way ``--control`` does."""
    attach_controls(flavour, control_files_from_paths(paths))


def apply_control_overlays(flavour: Any, overlay_paths: Sequence[Path | str]) -> None:
    """Apply ``target.kind: Control`` overlays onto loaded Flavour ``_controls``."""
    spec = flavour_spec_of(flavour)
    carried = spec.get("_controls") if isinstance(spec, dict) else None
    if not isinstance(carried, list) or not carried:
        return
    from mas.ctl.overlay.merge import merge_overlay

    for item in overlay_paths:
        path = as_path(item)
        data = read_mapping(path, what="overlay file")
        if not isinstance(data, dict) or data.get("kind") != "Overlay":
            continue
        target = (data.get("spec") or {}).get("target") or {}
        if str(target.get("kind") or "").strip().lower() != "control":
            continue
        want = str(target.get("name") or "").strip()
        patched: list[dict[str, Any]] = []
        matched = 0
        for row in carried:
            name = str(row.get("name") or "")
            if want and name != want:
                patched.append(row)
                continue
            base = {
                "apiVersion": "control/v1",
                "kind": "Control",
                "metadata": {"name": name},
                "spec": copy.deepcopy(row.get("spec") or {}),
            }
            merged = merge_overlay(base, data)
            updated = copy.deepcopy(row)
            updated["spec"] = merged.get("spec") or {}
            for where, message in [
                *duplicate_plugin_id_findings(updated["spec"]),
                *priority_findings(updated["spec"]),
            ]:
                raise ControlError(f"overlay {path.name} {where} {message}")
            patched.append(updated)
            matched += 1
        if want and matched == 0:
            raise ControlError(f"overlay {path.name} targets Control {want!r}, which this Flavour does not attach")
        carried[:] = patched


def session_flavour_with_controls(
    flavour: Any,
    *,
    overlay_paths: Sequence[Path | str] = (),
    controls: Sequence[str] = (),
    search_dirs: Sequence[Path | str] = (),
) -> Any:
    """A copy of *flavour* with Control files and ``--control`` rows attached. The original is unchanged."""
    if not overlay_paths and not controls:
        return flavour
    if not isinstance(flavour, dict):
        raise ControlError("Controls attach to a Flavour spec mapping")
    session = copy.deepcopy(flavour)
    attach_control_files(session, overlay_paths)
    apply_control_overlays(session, overlay_paths)
    if controls:
        attach_cli_controls(session, controls, search_dirs=search_dirs)
    return session


def session_controls_from_overlay_paths(paths: Sequence[Path | str], *, warn: bool = True) -> list[dict[str, Any]]:
    """Controls named by ``-o``: a ``kind: Control`` file, or an Overlay's ``spec.control`` refs. Deprecated.

    ``warn`` prints the deprecation. Experiment overlay stacks use :func:`attach_control_files` instead.
    """
    extra: list[dict[str, Any]] = []
    for item in paths:
        path = as_path(item)
        data = read_mapping(path, what="overlay file")
        if is_control_document(data):
            if warn:
                warn_once(f"-o {path.name} is a Control; passing a Control with -o is deprecated, use --control")
            extra.append(row_from_data(path, data))
            continue
        if not isinstance(data, dict) or data.get("kind") != "Overlay":
            continue
        rows = (data.get("spec") or {}).get("control")
        if not isinstance(rows, list):
            continue
        if warn:
            warn_once(
                f"Overlay {path.name} attaches Controls with spec.control; that is deprecated, "
                "attach them with --control or a Flavour"
            )
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not str(row.get("ref") or "").strip():
                raise ControlError(f"Overlay {str(path)!r} spec.control[{index}] needs ref, a Control file")
            extra.append(
                control_row(
                    {"ref": str(row["ref"]).strip(), "agents": row.get("agents")},
                    [path.parent],
                )
            )
    return extra


def attach_overlay_controls(flavour: Any, overlay_paths: Sequence[Path | str], *, warn: bool = True) -> None:
    """Attach the Controls named by ``-o`` (deprecated) onto the session Flavour."""
    attach_controls(flavour, session_controls_from_overlay_paths(overlay_paths, warn=warn))
    apply_control_overlays(flavour, overlay_paths)


def apply_resolved_controls(
    manifest: dict[str, Any] | None,
    rows: Sequence[dict[str, Any]],
    *,
    agent_id: str,
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
    warn: bool = True,
) -> dict[str, Any]:
    """Resolve matching Controls for *agent_id*. The agent spec is unchanged."""
    if not rows:
        return manifest if isinstance(manifest, dict) else {"spec": {}}
    applied, warnings = apply_controls(
        manifest,
        rows,
        agent_id=agent_id,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
    )
    if warn:
        for message in warnings:
            warn_once(message)
    return applied


def apply_overlay_controls_to_manifest(
    manifest: dict[str, Any] | None,
    overlay_paths: Sequence[Path | str],
    *,
    agent_id: str,
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
    warn: bool = True,
    control_rows: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve ``--control`` rows and ``-o`` Controls (deprecated). The agent spec is unchanged.

    ``entry_id`` is the session entry agent. Omitted, ``$entry`` matches nobody.
    """
    return apply_resolved_controls(
        manifest,
        [*session_controls_from_overlay_paths(overlay_paths, warn=warn), *control_rows],
        agent_id=agent_id,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
        warn=warn,
    )


def apply_flavour_controls(
    manifest: dict[str, Any] | None,
    flavour: Any,
    *,
    agent_id: str,
    search_dirs: Sequence[Path | str] = (),
    entry_id: str = "",
    delegate_ids: Sequence[str] = (),
    warn: bool = True,
) -> dict[str, Any]:
    """Resolve Flavour control rows. Matching Controls stay on the Flavour, not the agent."""
    return apply_resolved_controls(
        manifest,
        resolve_controls(flavour, search_dirs=search_dirs),
        agent_id=agent_id,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
        warn=warn,
    )
