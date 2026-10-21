#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Read/write catalog items through layouts. One implementation for new and deprecated routes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from mas.ctl.overlay.canvas import (
    apply_ui_canvas_to_overlay_yaml,
    apply_ui_canvas_to_yaml,
    overlay_is_layout_only,
    persist_overlay_canvas_yaml,
    persist_ui_canvas_yaml,
)
from mas.lab.controller.kinds import KIND_CATALOG
from mas.lab.controller.manifest_layout import (
    FileWrite,
    ItemBundle,
    apply_writes,
    item_ref,
    layout_for,
    versions_of,
)
from mas.lab.controller.path_safety import (
    UnsafeNameError,
    parse_item_id,
    validate_name,
    validate_version,
)
from mas.lab.controller.scope_catalog import Scope, ScopeCatalog, get_scope_catalog
from mas.version_spec import version_satisfies


def _overlay_is_layout_only(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        from mas.runtime.spec.source import load_yaml_file

        return overlay_is_layout_only(load_yaml_file(path))
    except Exception:
        return False


def _registry_for(store: Any | None):
    from mas.lab.controller.lab_registry import LabRegistry, get_lab_registry

    if store is not None:
        registry = getattr(store, "_registry", None)
        if isinstance(registry, LabRegistry):
            return registry
    return get_lab_registry()


def _manifest_name(yaml_text: str, fallback: str) -> str:
    try:
        doc = yaml.safe_load(yaml_text) or {}
    except Exception:
        return fallback
    if not isinstance(doc, dict):
        return fallback
    return str((doc.get("metadata") or {}).get("name") or fallback).strip() or fallback


def _layout_overlays_for(
    store: Any | None, scope: Scope, *, kind: str, names: list[str]
) -> list[tuple[Path, dict[str, Any]]]:
    registry = _registry_for(store)
    seen: set[Path] = set()
    matched: list[tuple[Path, dict[str, Any]]] = []
    for name in names:
        if not name:
            continue
        for path, doc in registry.layout_overlays_targeting(scope.root, kind=kind, name=name):
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            matched.append((path, doc))
    return matched


def _project_application_canvas(
    scope: Scope, bundle: ItemBundle, *, store: Any | None = None
) -> None:
    """Project registered layout-only canvas overlays onto a MAS item."""
    mas_name = _manifest_name(bundle.manifest, bundle.name)
    matched = _layout_overlays_for(
        store, scope, kind="MAS", names=[mas_name, bundle.name]
    )
    mas_yaml, agents, _overlay_path = apply_ui_canvas_to_yaml(
        bundle.root or scope.root, bundle.manifest, bundle.children, overlays=matched
    )
    bundle.manifest = mas_yaml
    bundle.children = agents


def _project_overlay_canvas(
    scope: Scope, bundle: ItemBundle, *, store: Any | None = None
) -> None:
    """Project a registered layout-only canvas overlay onto a spec overlay."""
    name = _manifest_name(bundle.manifest, bundle.name)
    matched = _layout_overlays_for(store, scope, kind="Overlay", names=[name, bundle.name])
    yaml_out, _path = apply_ui_canvas_to_overlay_yaml(
        bundle.manifest_path.parent if bundle.manifest_path is not None else scope.root,
        bundle.manifest,
        overlays=matched,
    )
    bundle.manifest = yaml_out


class ItemError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _catalog(store: Any | None = None) -> ScopeCatalog:
    return get_scope_catalog(store)


def _scope(scope_id: str, store: Any | None = None) -> Scope:
    try:
        return _catalog(store).get(scope_id)
    except KeyError as exc:
        raise ItemError(404, f"scope {scope_id!r} not found") from exc


def _default_model() -> str | None:
    try:
        from mas.lab.controller.lab_registry import get_lab_registry

        model = get_lab_registry().default_model()
        return str(model) if model else None
    except Exception:
        return None


def group_items(scope: Scope, kind: str, *, q: str | None = None) -> list[dict[str, Any]]:
    layout = layout_for(kind)
    located = layout.list_located(scope)
    by_name: dict[str, list] = {}
    for item in located:
        by_name.setdefault(item.name, []).append(item)
    groups: list[dict[str, Any]] = []
    needle = (q or "").strip().lower()
    for name, items in sorted(by_name.items()):
        versions, latest = versions_of(items)
        if needle and needle not in name.lower() and not any(
            needle in (i.title or "").lower() or needle in (i.summary or "").lower() for i in items
        ):
            continue
        listed = []
        for i in items:
            row: dict[str, Any] = {
                "id": i.item_id,
                "version": i.version,
                "ref": item_ref(scope, i.item_id),
                "writable": scope.writable,
            }
            if kind == "overlay":
                row["ui_canvas"] = _overlay_is_layout_only(i.manifest_path)
            listed.append(row)
        group: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "title": items[0].title or name,
            "summary": items[0].summary,
            "versions": versions,
            "latest": latest,
            "items": listed,
        }
        if kind == "overlay":
            group["ui_canvas"] = any(row.get("ui_canvas") for row in listed)
        groups.append(group)
    return groups


def _safe_item_id(item: str) -> str:
    try:
        name, version = parse_item_id(item)
    except UnsafeNameError as exc:
        raise ItemError(400, str(exc)) from exc
    return f"{name}@{version}" if version else name


def _next_version(latest: str | None) -> str:
    if not latest:
        return "v1"
    major = latest.lstrip("v").split(".")[0]
    try:
        return f"v{int(major) + 1}"
    except ValueError:
        return "v2"


def get_item(scope_id: str, kind: str, item: str, *, store: Any | None = None) -> dict[str, Any]:
    item = _safe_item_id(item)
    scope = _scope(scope_id, store)
    layout = layout_for(kind)
    bundle = layout.read(scope, item)
    if bundle is None:
        raise ItemError(404, f"{kind} {item!r} not found")
    if kind == "application":
        _project_application_canvas(scope, bundle, store=store)
    elif kind == "overlay":
        _project_overlay_canvas(scope, bundle, store=store)
    payload = bundle.public_dict()
    if kind == "overlay":
        payload["ui_canvas"] = _overlay_is_layout_only(bundle.manifest_path)
    return payload


def check_name(
    scope_id: str, kind: str, name: str, version: str | None = None, *, store: Any | None = None
) -> dict[str, Any]:
    try:
        validate_name(name)
        validate_version(version)
    except UnsafeNameError as exc:
        return {"ok": False, "reason": str(exc)}
    scope = _scope(scope_id, store)
    if not scope.writable:
        return {"ok": False, "reason": "read_only", "copy_into": _copy_into_ids(store, kind)}
    item_id = f"{name}@{version}" if version else name
    if layout_for(kind).locate(scope, item_id) is not None:
        return {"ok": False, "reason": "exists"}
    return {"ok": True, "id": item_id, "ref": item_ref(scope, item_id)}


def _copy_into_ids(store: Any | None = None, kind: str | None = None) -> list[str]:
    scopes = [s for s in _catalog(store).all() if s.writable]
    if kind:
        scopes = [s for s in scopes if kind in (s.kinds or [])]
    return [s.id for s in scopes]


def _read_only_error(store: Any | None = None, kind: str | None = None) -> ItemError:
    return ItemError(403, {"reason": "read_only", "copy_into": _copy_into_ids(store, kind)})


def create_item(
    scope_id: str,
    *,
    kind: str,
    name: str,
    version: str | None = None,
    template: str | None = None,
    parent: str | None = None,
    upload_id: str | None = None,
    store: Any | None = None,
) -> dict[str, Any]:
    if kind not in KIND_CATALOG:
        raise ItemError(400, f"unknown kind {kind!r}")
    if not KIND_CATALOG[kind].get("creatable"):
        raise ItemError(400, f"{kind} is not creatable")
    try:
        name = validate_name(name)
        version = validate_version(version)
    except UnsafeNameError as exc:
        raise ItemError(400, str(exc)) from exc
    scope = _scope(scope_id, store)
    if not scope.writable:
        raise _read_only_error(store, kind)
    layout = layout_for(kind)
    item_id = f"{name}@{version}" if version else name
    if layout.locate(scope, item_id) is not None:
        raise ItemError(409, f"{kind} {item_id!r} already exists")
    copy_from = None
    if template and template not in ("blank", ""):
        copy_from = layout.read(scope, template)
        if copy_from is None:
            raise ItemError(404, f"template {template!r} not found")
    initial_manifest, extra_writes = _payload_from_upload(scope, layout, name, version, upload_id)
    return _commit_create(
        scope,
        kind,
        name,
        version,
        template=template,
        parent=parent,
        copy_from=copy_from,
        initial_manifest=initial_manifest,
        extra_writes=extra_writes,
        store=store,
    )


def _payload_from_upload(
    scope: Scope,
    layout: Any,
    name: str,
    version: str | None,
    upload_id: str | None,
) -> tuple[str | None, list[FileWrite] | None]:
    if not upload_id:
        return None, None
    from mas.lab.controller.uploads import get_upload_store

    uploaded = get_upload_store().get(upload_id)
    if uploaded is None:
        raise ItemError(404, f"upload {upload_id!r} not found")
    suffix = Path(uploaded.name).suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return uploaded.path.read_text(encoding="utf-8"), None
    dest = layout.default_create_root(scope, name, version) / f"{name}{suffix or '.dat'}"
    return None, [FileWrite(dest, source=uploaded.path)]


def _commit_create(
    scope: Scope,
    kind: str,
    name: str,
    version: str | None,
    *,
    template: str | None = None,
    parent: str | None = None,
    copy_from: ItemBundle | None = None,
    initial_manifest: str | None = None,
    extra_writes: list | None = None,
    store: Any | None = None,
) -> dict[str, Any]:
    layout = layout_for(kind)
    item_id = f"{name}@{version}" if version else name
    writes = layout.plan_create(
        scope,
        name=name,
        version=version,
        template=template,
        parent=parent,
        default_model=_default_model(),
        copy_from=copy_from,
    )
    if initial_manifest:
        for write in writes:
            if not write.delete and write.path.suffix in {".yaml", ".yml"}:
                write.content = initial_manifest
                break
    if extra_writes:
        writes = list(writes) + list(extra_writes)
    _validate_writes(scope, kind, writes)
    apply_writes(scope, writes)
    _invalidate(store)
    bundle = layout.read(scope, item_id)
    if bundle is None:
        raise ItemError(500, "write succeeded but the item could not be read back")
    return bundle.public_dict()


def update_item(
    scope_id: str,
    kind: str,
    item: str,
    *,
    manifest: str,
    children: dict[str, str] | None = None,
    revision: str,
    store: Any | None = None,
) -> dict[str, Any]:
    item = _safe_item_id(item)
    scope = _scope(scope_id, store)
    if not scope.writable:
        raise _read_only_error(store, kind)
    layout = layout_for(kind)
    current = layout.read(scope, item)
    located = layout.locate(scope, item)
    if current is None or located is None:
        raise ItemError(404, f"{kind} {item!r} not found")
    if current.revision != revision:
        raise ItemError(
            409,
            {"reason": "stale_revision", "revision": current.revision, "item": current.public_dict()},
        )
    current.manifest = manifest
    current.children = children or {}
    if kind == "application" and current.root is not None:
        mas_yaml, agents = persist_ui_canvas_yaml(
            current.root, current.manifest, current.children, mas_name=current.name
        )
        current.manifest = mas_yaml
        current.children = agents
    elif kind == "overlay" and current.manifest_path is not None:
        current.manifest = persist_overlay_canvas_yaml(
            current.manifest_path.parent, current.manifest, overlay_name=current.name
        )
    writes = layout.plan_update(scope, current, located)
    _validate_writes(scope, kind, writes)
    apply_writes(scope, writes)
    _invalidate(store)
    bundle = layout.read(scope, item)
    if bundle is None:
        raise ItemError(500, "update succeeded but the item could not be read back")
    return bundle.public_dict()


def delete_item(scope_id: str, kind: str, item: str, *, store: Any | None = None) -> None:
    item = _safe_item_id(item)
    scope = _scope(scope_id, store)
    if not scope.writable:
        raise _read_only_error(store, kind)
    layout = layout_for(kind)
    located = layout.locate(scope, item)
    if located is None:
        raise ItemError(404, f"{kind} {item!r} not found")
    apply_writes(scope, layout.plan_delete(scope, located))
    _invalidate(store)


def fork_item(
    scope_id: str,
    kind: str,
    item: str,
    *,
    version: str | None = None,
    name: str | None = None,
    target_scope: str | None = None,
    store: Any | None = None,
) -> dict[str, Any]:
    item = _safe_item_id(item)
    source_scope = _scope(scope_id, store)
    dest_scope = _scope(target_scope, store) if target_scope else source_scope
    if not dest_scope.writable:
        raise _read_only_error(store, kind)
    layout = layout_for(kind)
    bundle = layout.read(source_scope, item)
    if bundle is None:
        raise ItemError(404, f"{kind} {item!r} not found")
    try:
        new_name = validate_name(name) if name else bundle.name
        new_version = validate_version(version) if version else bundle.version
    except UnsafeNameError as exc:
        raise ItemError(400, str(exc)) from exc
    if new_version is None and KIND_CATALOG.get(kind, {}).get("versioned"):
        _versions, latest = layout.versions_for(dest_scope, new_name)
        new_version = _next_version(latest)
    if dest_scope.id == source_scope.id:
        return create_item(
            dest_scope.id,
            kind=kind,
            name=new_name,
            version=new_version,
            template=bundle.item_id,
            store=store,
        )
    return _copy_across(dest_scope, kind, new_name, new_version, bundle, store)


def _copy_across(
    dest: Scope, kind: str, name: str, version: str | None, bundle: ItemBundle, store: Any | None
) -> dict[str, Any]:
    layout = layout_for(kind)
    item_id = f"{name}@{version}" if version else name
    if layout.locate(dest, item_id) is not None:
        raise ItemError(409, f"{kind} {item_id!r} already exists")
    return _commit_create(dest, kind, name, version, copy_from=bundle, store=store)


def rename_item(
    scope_id: str,
    kind: str,
    item: str,
    *,
    name: str,
    store: Any | None = None,
) -> dict[str, Any]:
    item = _safe_item_id(item)
    scope = _scope(scope_id, store)
    if not scope.writable:
        raise _read_only_error(store, kind)
    new_name = validate_name(name)
    layout = layout_for(kind)
    bundle = layout.read(scope, item)
    located = layout.locate(scope, item)
    if bundle is None or located is None:
        raise ItemError(404, f"{kind} {item!r} not found")
    old_ref = bundle.ref
    new_id = f"{new_name}@{bundle.version}" if bundle.version else new_name
    new_ref = item_ref(scope, new_id)
    if layout.locate(scope, new_id) is not None:
        raise ItemError(409, f"{kind} {new_id!r} already exists")
    writes = layout.plan_create(
        scope,
        name=new_name,
        version=bundle.version,
        template=None,
        parent=None,
        default_model=None,
        copy_from=bundle,
    )
    changed = [new_id]
    for other_kind in KIND_CATALOG:
        if other_kind == kind:
            continue
        try:
            other_layout = layout_for(other_kind)
        except KeyError:
            continue
        for other in other_layout.list_located(scope):
            if not other.manifest_path.is_file():
                continue
            text = other.manifest_path.read_text(encoding="utf-8")
            updated = _rewrite_token(text, old_ref, new_ref)
            updated = _rewrite_token(updated, bundle.item_id, new_id)
            if updated == text:
                continue
            writes.append(FileWrite(other.manifest_path, updated))
            changed.append(other.item_id)
    writes.extend(layout.plan_delete(scope, located))
    _validate_writes(scope, kind, writes)
    apply_writes(scope, writes)
    _invalidate(store)
    created = layout.read(scope, new_id)
    if created is None:
        raise ItemError(500, "rename succeeded but the item could not be read back")
    return {"id": created.item_id, "ref": created.ref, "changed_items": changed}


def _rewrite_token(text: str, old: str, new: str) -> str:
    """Replace *old* only when it is not a substring of a longer identifier."""
    if not old or old == new or old not in text:
        return text
    pattern = re.compile(rf"(?<![\w./:@-]){re.escape(old)}(?![\w./:@-])")
    return pattern.sub(new, text)


def resolve_ref(ref: str, *, store: Any | None = None) -> dict[str, Any]:
    from mas.version_spec import split_library_prefix

    scheme, rest = split_library_prefix(ref)
    catalog = _catalog(store)
    if scheme:
        try:
            scope = catalog.get(f"library:{scheme}")
        except KeyError:
            try:
                scope = catalog.get(f"lab:{scheme}")
            except KeyError as exc:
                raise ItemError(404, f"unknown library {scheme!r} in ref") from exc
    else:
        raise ItemError(400, "ref must be of the form library:item")
    name, spec = _parse_id_or_range(rest)
    # Try application first, then other kinds.
    for kind in ("application", "dataset", "tool", "overlay", "pipeline", "experiment", "agent", "skill"):
        layout = layout_for(kind)
        located = [i for i in layout.list_located(scope) if i.name == name]
        if not located:
            continue
        chosen = _pick_version(located, spec)
        if chosen is None:
            continue
        bundle = layout.read(scope, chosen.item_id)
        if bundle is None:
            continue
        return {
            "ref": ref,
            "resolved_id": bundle.item_id,
            "scope": scope.id,
            "kind": kind,
            "item": bundle.public_dict(),
        }
    raise ItemError(404, f"could not resolve {ref!r}")


def _parse_id_or_range(rest: str) -> tuple[str, str | None]:
    if "@" not in rest:
        return rest, None
    name, _, spec = rest.rpartition("@")
    return name, spec or None


def _pick_version(located: list, spec: str | None):
    if spec is None or spec.lower() == "latest":
        versioned = [i for i in located if i.version]
        if versioned:
            from mas.version_spec import version_sort_key

            return max(versioned, key=lambda i: version_sort_key(i.version or "v0"))
        return located[0]
    if spec.startswith("^") or any(spec.startswith(op) for op in (">=", "<=", ">", "<", "~=")):
        matches = [i for i in located if i.version and version_satisfies(i.version, spec)]
        if not matches:
            return None
        from mas.version_spec import version_sort_key

        return max(matches, key=lambda i: version_sort_key(i.version or "v0"))
    for item in located:
        if item.version == spec or item.item_id.endswith(f"@{spec}"):
            return item
    return None


def _validate_writes(scope: Scope, kind: str, writes: list) -> None:
    from mas.lab.controller.manifest_validation import validate_manifest_yaml_content

    errors: list[str] = []
    for write in writes:
        if write.delete or not write.content.strip():
            continue
        result = validate_manifest_yaml_content(
            write.content,
            base_dir=scope.root,
            resolve_refs=False,
        )
        if not result.get("valid", True):
            errors.append(result.get("stderr") or "invalid manifest")
    if errors:
        raise ItemError(422, {"reason": "invalid_manifest", "errors": errors})


def _invalidate(store: Any | None) -> None:
    if store is not None and hasattr(store, "invalidate"):
        store.invalidate()
    else:
        try:
            from mas.lab.controller.deps import get_manifest_store

            get_manifest_store().invalidate()
        except Exception:
            pass
