#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load kind: Control documents and the Flavour rows that name them."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mas.ctl.infra.bind import flavour_spec_of
from mas.ctl.session.flavour_control.error import ControlError
from mas.ctl.session.flavour_control.refs import (
    ABSOLUTE_HINT,
    STAY_UNDER,
    absolute_path_findings,
    as_path,
    control_search_dirs,
    find_control,
    is_absolute_path,
    read_mapping,
    ref_walks_up,
)
from mas.ctl.session.flavour_control.spec import (
    GOVERNANCE,
    OBSERVABILITY,
    leftover_pipeline_error,
    leftover_spec_key,
)


def validate_control_document(data: Any, path: Path) -> dict[str, Any]:
    """Schema-validate a loaded mapping and refuse absolute paths inside it."""
    if not isinstance(data, dict) or data.get("kind") != "Control":
        raise ControlError(f"Control file {str(path)!r} is not a kind: Control document")
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}
    leftover = leftover_spec_key(spec)
    if leftover:
        raise ControlError(leftover_pipeline_error(str(path), leftover))
    from mas.ctl.validate import validate_data, validation_enabled

    if validation_enabled():
        result = validate_data(data, kind="control")
        try:
            result.raise_if_failed()
        except ValueError as exc:
            raise ControlError(f"Control {str(path)!r} is invalid: {exc}") from exc
    from mas.ctl.session.flavour_control.plugins import (
        duplicate_plugin_id_findings,
        keyed_items,
        priority_findings,
        resolve_pipeline_item,
        undeclared_path_findings,
    )

    for where, message in [*duplicate_plugin_id_findings(spec), *priority_findings(spec)]:
        raise ControlError(f"Control {str(path)!r} {where} {message}")

    for key, item in keyed_items(spec):
        resolve_pipeline_item(item, key=key)
    for where, message in undeclared_path_findings(spec):
        raise ControlError(f"Control {str(path)!r} {where} {message}")
    for where, message in absolute_path_findings(data):
        raise ControlError(f"Control {str(path)!r} {where} is {message}")
    return data


def load_control(path: Path | str) -> dict[str, Any]:
    """Load, schema-validate, and return a ``kind: Control`` document."""
    file = as_path(path)
    return validate_control_document(read_mapping(file, what="Control file"), file)


def is_control_document(data: Any) -> bool:
    """True when *data* is a ``kind: Control`` mapping."""
    return isinstance(data, dict) and str(data.get("kind") or "") == "Control"


def flavour_control_rows(flavour: Any) -> list[dict[str, Any]]:
    """Flavour ``spec.control`` as ``{ref, agents}`` rows.

    A leftover mapping (the old unread Flavour ``control`` block) is ignored.
    """
    spec = flavour_spec_of(flavour)
    raw = spec.get("control")
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("ref") or "").strip():
            raise ControlError(f"Flavour spec.control[{index}] needs ref, a Control file")
        extra = set(item) - {"ref", "agents", "settings"}
        if extra:
            raise ControlError(f"Flavour spec.control[{index}] unknown keys {sorted(extra)}")
        ref = str(item["ref"]).strip()
        if is_absolute_path(ref):
            raise ControlError(f"Flavour spec.control[{index}].ref is the absolute path {ref!r}; {ABSOLUTE_HINT}")
        if ref_walks_up(ref):
            raise ControlError(f"Flavour spec.control[{index}].ref {ref!r} {STAY_UNDER}; {ABSOLUTE_HINT}")
        settings = item.get("settings")
        if settings is not None and not (
            isinstance(settings, dict) and all(isinstance(v, dict) for v in settings.values())
        ):
            raise ControlError(f"Flavour spec.control[{index}].settings maps a plugin name to its settings")
        row = {"ref": ref, "agents": item.get("agents")}
        if settings:
            row["settings"] = {str(name): dict(params) for name, params in settings.items()}
        rows.append(row)
    return rows


def apply_settings(document: dict[str, Any], settings: dict[str, dict[str, Any]], path: Path) -> dict[str, Any]:
    """The Control with a Flavour row's per-plugin settings merged over the plugin's own.

    This is how one Control file gets different settings on different agents: each Flavour row names the
    plugins to adjust. A name the Control does not list is an error, and the result is validated again.
    """
    spec = copy.deepcopy(dict(document.get("spec") or {}))
    from mas.ctl.session.flavour_control.plugins import canonical_plugin_id, parse_plugin_item

    for name, params in settings.items():
        found = False
        for key in (OBSERVABILITY, GOVERNANCE):
            items = spec.get(key)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                own_name, own_params = parse_plugin_item(item)
                if canonical_plugin_id(own_name, key) == canonical_plugin_id(name, key):
                    items[index] = {own_name: {**own_params, **params}}
                    found = True
        if not found:
            raise ControlError(f"Flavour settings name plugin {name!r}, which Control {str(path)!r} does not list")
    return validate_control_document({**document, "spec": spec}, path)


def row_from_loaded(
    path: Path, document: dict[str, Any], *, agents: Any, settings: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    if settings:
        document = apply_settings(document, settings, path)
    spec = dict(document.get("spec") or {})
    chosen = agents if agents is not None else spec.get("agents")
    if chosen is None:
        chosen = "$all"
    resolved = path.resolve() if path.exists() else path
    row = {
        "ref": str(resolved),
        "agents": chosen,
        "spec": spec,
        "name": str((document.get("metadata") or {}).get("name") or path.stem),
    }
    if settings:
        row["settings"] = settings
    return row


def row_from_data(path: Path, data: Any, *, agents: Any = None) -> dict[str, Any]:
    return row_from_loaded(path, validate_control_document(data, path), agents=agents)


def control_row(row: dict[str, Any], search_dirs: Sequence[Path], *, from_cli: bool = False) -> dict[str, Any]:
    path = find_control(row["ref"], search_dirs, from_cli=from_cli)
    return row_from_loaded(path, load_control(path), agents=row["agents"], settings=row.get("settings"))


def control_file(row: dict[str, Any]) -> str:
    """Resolved path of a loaded Control row, used so one file's layers attach to an agent once."""
    ref = str(row.get("ref") or "")
    if not ref:
        return ""
    path = Path(ref)
    return str(path.resolve()) if path.exists() else ref


def control_key(row: dict[str, Any]) -> str:
    """Identity of a loaded Control row: the resolved file and the agents it attaches to.

    The same file attached to different agents is two attachments, and so is the same file with different settings;
    the same file attached to the same agents with the same settings twice is one.
    """
    agents = row.get("agents")
    selector = sorted(str(item) for item in agents) if isinstance(agents, list) else agents
    return json.dumps([control_file(row), selector, row.get("settings") or {}], sort_keys=True)


def unique_controls(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last row for each Control file and selector wins: ``--control`` of a Flavour ref does not attach twice."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, row in enumerate(rows):
        key = control_key(row) or f"anon:{index}"
        if key in by_key:
            order.remove(key)
        order.append(key)
        by_key[key] = copy.deepcopy(row)
    return [by_key[key] for key in order]


def hydrate_flavour_controls(flavour: Any, *, source_dir: Path | str | None = None) -> None:
    """Resolve Control refs against ``source_dir`` (the Flavour file) and store the bodies on the spec.

    Relative refs never use the current directory. Call this once when the Flavour
    loads so the run and the hash see the same bodies.
    """
    spec = flavour_spec_of(flavour)
    if not isinstance(spec, dict):
        return
    rows = flavour_control_rows(flavour)
    if not rows:
        spec.pop("_controls", None)
        return
    spec["_controls"] = unique_controls([control_row(row, control_search_dirs(source_dir)) for row in rows])


def resolve_controls(
    flavour: Any,
    *,
    search_dirs: Sequence[Path | str] = (),
) -> list[dict[str, Any]]:
    """Load each Flavour ``spec.control`` row. Returns ``{ref, agents, spec, name}``.

    A Flavour loaded from a file already carries ``_controls``. Unhydrated inline
    specs still resolve against ``search_dirs``.
    """
    spec = flavour_spec_of(flavour)
    carried = spec.get("_controls")
    if isinstance(carried, list):
        return unique_controls(carried)
    dirs = control_search_dirs(*search_dirs)
    return unique_controls([control_row(row, dirs) for row in flavour_control_rows(flavour)])


def hash_control_body(item: dict[str, Any]) -> dict[str, Any]:
    """What a Control contributes to the run hash.

    Observability and governance constrain the run.
    """
    spec = item.get("spec") or {}
    body: dict[str, Any] = {"agents": item.get("agents")}
    if OBSERVABILITY in spec:
        body[OBSERVABILITY] = copy.deepcopy(spec[OBSERVABILITY])
    if GOVERNANCE in spec:
        body[GOVERNANCE] = copy.deepcopy(spec[GOVERNANCE])
    return body


def control_body_for_hash(flavour: Any, *, search_dirs: Sequence[Path | str] = ()) -> list[dict[str, Any]] | None:
    """The Control plugin lists and agent selectors a run applies, for the run hash; ``None`` when there are none.

    Bodies resolved when the Flavour loaded (or added from the command line) are used as they are. An inline
    spec that was never loaded resolves its refs against ``search_dirs``, never the current directory, and a ref
    that cannot be found is an error rather than a hash that ignores the Control.
    """
    spec = flavour_spec_of(flavour)
    if not isinstance(spec.get("_controls"), list) and not isinstance(spec.get("control"), list):
        return None
    return [hash_control_body(item) for item in resolve_controls(flavour, search_dirs=search_dirs)]


def control_row_from_path(path: Path | str, *, agents: Any = None) -> dict[str, Any]:
    """Load one Control file as a resolved ``{ref, agents, spec, name}`` row."""
    file = as_path(path)
    return row_from_loaded(file, load_control(file), agents=agents)


def control_files_from_paths(paths: Sequence[Path | str]) -> list[dict[str, Any]]:
    """``kind: Control`` files in *paths* as Flavour rows, the same way ``--control`` attaches.

    Overlay files are skipped. Experiment ``overlays.control`` uses this path, not ``-o``.
    """
    extra: list[dict[str, Any]] = []
    for item in paths:
        path = as_path(item)
        data = read_mapping(path, what="Control file")
        if is_control_document(data):
            extra.append(row_from_data(path, data))
    return extra
