#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent overlay merge — ported from mas-lab runtime/manifest/composition.py (RFC 7396 + agent rules)."""

from __future__ import annotations

import logging
from copy import deepcopy
from functools import lru_cache
from typing import Any

import yaml
from mas.ctl.validate.schemas import schema_root
from mas.runtime.spec.plugin_binding import normalize_plugin_binding, plugin_binding_id

logger = logging.getLogger(__name__)

_OVERLAY_OP_KEY = "$op"
_PATCH_OP_REPLACE = "replace"
_PATCH_OP_ADD = "add"
_PATCH_OP_REMOVE = "remove"
_PATCH_OP_CLEAR = "clear"
_PATCH_OP_MERGE = "merge"
_PATCH_OPS = frozenset({_PATCH_OP_REPLACE, _PATCH_OP_ADD, _PATCH_OP_REMOVE, _PATCH_OP_CLEAR, _PATCH_OP_MERGE})


# Every kind reads merge metadata straight off its own canonical schema --
# every overlay-patchable field carries its own x-merge annotation inline on
# the field it actually describes, so adding a new patchable field is a
# single edit in a single file (docs/schemas/runtime/<kind>.schema.yaml),
# not a second hand-maintained overlay-*-patch fragment kept in sync by hand.
_OVERLAY_PATCH_SCHEMA_FILES: dict[str, str] = {
    "Agent": "runtime/agent.schema.yaml",
    "MAS": "runtime/mas.schema.yaml",
    "Flavour": "runtime/flavour.schema.yaml",
    "Infra": "runtime/infra.schema.yaml",
}

# Where to start reading "properties" from, per schema file, relative to that
# file's own root -- every canonical kind schema nests its patchable fields
# one level deeper (properties.spec.properties.*) than the root.
_OVERLAY_PATCH_SCHEMA_ROOT: dict[str, tuple[str, ...]] = {
    "Agent": ("properties", "spec"),
    "MAS": ("properties", "spec"),
    "Flavour": ("properties", "spec"),
    "Infra": ("properties", "spec"),
}


@lru_cache(maxsize=8)
def _load_yaml_schema(rel_path: str) -> dict[str, Any]:
    schema_path = schema_root() / rel_path
    data = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _overlay_patch_root_schema(kind: str) -> dict[str, Any]:
    """The schema node whose ``properties`` are this kind's patchable fields."""
    rel_path = _OVERLAY_PATCH_SCHEMA_FILES.get(kind)
    if rel_path is None:
        return {}
    schema = _load_yaml_schema(rel_path)
    for key in _OVERLAY_PATCH_SCHEMA_ROOT.get(kind, ()):
        schema = schema.get(key) or {}
    return schema


def _format_semantic(meta: dict[str, Any]) -> str:
    strategy = str(meta.get("strategy") or "")
    extras: list[str] = []
    identity = meta.get("identity")
    if identity:
        extras.append(f"identity={identity}")
    if extras:
        return f"{strategy}({','.join(extras)})"
    return strategy


def _collect_merge_meta(
    schema: dict[str, Any],
    *,
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return out
    for key, raw_prop in props.items():
        if not isinstance(raw_prop, dict):
            continue
        path = f"{prefix}{key}"
        merge_meta = raw_prop.get("x-merge")
        if isinstance(merge_meta, dict):
            out[path] = dict(merge_meta)
        out.update(_collect_merge_meta(raw_prop, prefix=f"{path}."))
    return out


def _schema_property_keys(kind: str) -> frozenset[str]:
    schema = _overlay_patch_root_schema(kind)
    props = schema.get("properties")
    if not isinstance(props, dict):
        return frozenset()
    return frozenset(str(k) for k in props.keys())


@lru_cache(maxsize=3)
def _overlay_merge_meta(kind: str) -> dict[str, dict[str, Any]]:
    return _collect_merge_meta(_overlay_patch_root_schema(kind))


def overlay_runtime_semantics() -> dict[str, dict[str, str]]:
    """Return non-trivial overlay merge semantics derived from schema x-merge metadata."""
    out: dict[str, dict[str, str]] = {}
    for kind in ("Agent", "MAS", "Flavour", "Infra"):
        out[kind] = {field: _format_semantic(meta) for field, meta in _overlay_merge_meta(kind).items()}
    return out


def _schema_kind_from_target(target_kind: str) -> str | None:
    normalized = str(target_kind or "").strip().lower()
    if normalized in ("agent",):
        return "Agent"
    if normalized in ("mas", "app", "workflow"):
        return "MAS"
    if normalized in ("flavour",):
        return "Flavour"
    if normalized in ("infra",):
        return "Infra"
    return None


def _validate_patch_fields_against_target_schema(
    overlay: dict[str, Any],
    base: dict[str, Any] | None = None,
) -> None:
    spec = overlay.get("spec") or {}
    patch = spec.get("patch")
    if not isinstance(patch, dict):
        return
    schema_kind = _schema_kind_from_target(str((spec.get("target") or {}).get("kind") or ""))
    if schema_kind is None:
        return
    from mas.ctl.overrides.schema import assert_patch_content, load_target_schema

    try:
        assert_patch_content(
            load_target_schema(schema_kind),
            patch,
            root=schema_kind.lower(),
            instance=base,
        )
    except ValueError as error:
        raise OverlayTargetError(str(error)) from error


def _ops_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and isinstance(value.get(_OVERLAY_OP_KEY), dict):
        value = value[_OVERLAY_OP_KEY]
    if not isinstance(value, dict):
        return None
    if not (set(value) & _PATCH_OPS):
        return None
    return value


def _merge_list_ops(
    existing: list[Any],
    incoming: Any,
    *,
    dedupe_key=None,
) -> list[Any]:
    ops = _ops_dict(incoming)
    if ops is None:
        # Implicit replace ergonomics: raw list means "replace".
        if isinstance(incoming, list):
            return list(incoming)
        raise OverlayTargetError(
            "collection patch must be a raw list (implicit replace) or use '$op' (replace/add/remove/clear)"
        )

    if ops.get(_PATCH_OP_CLEAR) is True:
        result: list[Any] = []
    else:
        result = list(existing)

    if _PATCH_OP_REPLACE in ops:
        result = list(ops.get(_PATCH_OP_REPLACE) or [])

    if _PATCH_OP_REMOVE in ops:
        to_remove = list(ops.get(_PATCH_OP_REMOVE) or [])
        if dedupe_key is None:
            result = [item for item in result if item not in to_remove]
        else:
            remove_keys = {dedupe_key(item) for item in to_remove}
            result = [item for item in result if dedupe_key(item) not in remove_keys]

    if _PATCH_OP_ADD in ops:
        for item in list(ops.get(_PATCH_OP_ADD) or []):
            if dedupe_key is None:
                if item not in result:
                    result.append(item)
            else:
                keys = {dedupe_key(v) for v in result}
                key = dedupe_key(item)
                if key not in keys:
                    result.append(item)

    return result


def _as_fragment_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def merge_context_chunk(existing: Any, incoming: Any) -> Any:
    """Merge one spec.context.<key> overlay patch value onto its base value.

    A plain value (string/{ref}/list) fully replaces the base chunk -- implicit
    replace ergonomics, consistent with every other overlay merge strategy in
    this repo. A `{"$op": {replace|add|remove|clear}}` value instead operates on
    the base chunk's fragment list via the same list-patch semantics spec.tools
    already uses (_merge_list_ops above), coercing a scalar base value to a
    one-item list first -- so an overlay can add or remove a single fragment
    (e.g. append one sentence to spec.context.role) without restating the rest.
    """
    if _ops_dict(incoming) is None:
        return deepcopy(incoming)
    return _merge_list_ops(_as_fragment_list(existing), incoming)


def merge_context_map(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge an overlay's spec.context patch onto the base spec.context map."""
    merged = deepcopy(existing) if isinstance(existing, dict) else {}
    for key, value in incoming.items():
        if value is None:
            merged.pop(key, None)
            continue
        merged[key] = merge_context_chunk(merged.get(key), value)
    return merged


def _merge_mapping_ops(existing: dict[str, Any], incoming: Any) -> dict[str, Any]:
    ops = _ops_dict(incoming)
    if ops is None:
        if isinstance(incoming, dict):
            # Implicit replace ergonomics for mapping fields.
            return deepcopy(incoming)
        raise OverlayTargetError(
            "mapping patch must be a raw object (implicit replace) or use '$op' (replace/merge/clear)"
        )
    if ops.get(_PATCH_OP_CLEAR) is True:
        result: dict[str, Any] = {}
    else:
        result = deepcopy(existing)
    if _PATCH_OP_REPLACE in ops:
        replace_val = ops.get(_PATCH_OP_REPLACE) or {}
        if not isinstance(replace_val, dict):
            raise OverlayTargetError("replace operation expects an object")
        result = deepcopy(replace_val)
    if _PATCH_OP_MERGE in ops:
        merge_val = ops.get(_PATCH_OP_MERGE) or {}
        if not isinstance(merge_val, dict):
            raise OverlayTargetError("merge operation expects an object")
        result = apply_merge_patch(result, deepcopy(merge_val))
    return result


def _plugin_entry_key(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and len(item) == 1:
        return str(next(iter(item.keys())))
    return str(item)


def _merge_plugin_list_ops(existing: list[Any], incoming: Any) -> list[Any]:
    ops = _ops_dict(incoming)
    if ops is None:
        if isinstance(incoming, list):
            return list(incoming)
        raise OverlayTargetError(
            "plugin-list patch must be a raw list (implicit replace) or use '$op' (replace/add/remove/clear)"
        )

    if ops.get(_PATCH_OP_CLEAR) is True:
        result: list[Any] = []
    else:
        result = list(existing)

    if _PATCH_OP_REPLACE in ops:
        result = list(ops.get(_PATCH_OP_REPLACE) or [])

    if _PATCH_OP_REMOVE in ops:
        remove_keys = {str(v) for v in list(ops.get(_PATCH_OP_REMOVE) or [])}
        result = [item for item in result if _plugin_entry_key(item) not in remove_keys]

    if _PATCH_OP_ADD in ops:
        keys = {_plugin_entry_key(item) for item in result}
        for item in list(ops.get(_PATCH_OP_ADD) or []):
            key = _plugin_entry_key(item)
            if key not in keys:
                result.append(item)
                keys.add(key)

    return result


_LIST_OP_STRATEGIES = frozenset({"list_ops", "named_list_union", "named_list_merge", "plugin_list_ops"})
_MAP_OP_STRATEGIES = frozenset({"mapping_ops", "mapping_merge_or_ops", "plugin_binding_merge"})


def _require_existing_list(existing: Any) -> list[Any]:
    if existing is None:
        return []
    if isinstance(existing, list):
        return list(existing)
    raise OverlayTargetError(
        f"$op cannot apply to a {type(existing).__name__} value; expected a list"
    )


def _require_existing_mapping(existing: Any) -> dict[str, Any]:
    if existing is None:
        return {}
    if isinstance(existing, dict):
        return existing
    raise OverlayTargetError(
        f"$op cannot apply to a {type(existing).__name__} value; expected a mapping"
    )


def _merge_value_by_meta(existing: Any, incoming: Any, meta: dict[str, Any]) -> Any:
    strategy = str(meta.get("strategy") or "")

    if incoming is None:
        return None

    ops = _ops_dict(incoming)
    if ops is not None:
        if strategy in _LIST_OP_STRATEGIES:
            existing = _require_existing_list(existing)
        elif strategy in _MAP_OP_STRATEGIES:
            existing = _require_existing_mapping(existing)
            if _PATCH_OP_ADD in ops or _PATCH_OP_REMOVE in ops:
                raise OverlayTargetError("$op add/remove requires a list field, not a mapping")
        elif strategy not in {"context_merge"}:
            raise OverlayTargetError(
                f"$op cannot apply to a {strategy or 'scalar'} field"
            )

    if strategy == "list_ops":
        existing_list = _require_existing_list(existing) if ops is not None else (
            list(existing) if isinstance(existing, list) else []
        )
        try:
            return _merge_list_ops(existing_list, incoming, dedupe_key=None)
        except ValueError as exc:
            raise OverlayTargetError(str(exc)) from exc

    if strategy == "named_list_union":
        identity = str(meta.get("identity") or "name")
        existing_list = list(existing or []) if isinstance(existing, list) else []

        def _item_key(item: Any) -> str:
            if isinstance(item, dict):
                if identity == "id":
                    return str(item.get("id") or item.get("model") or "main")
                return str(item.get(identity) or "")
            return str(item)

        if _ops_dict(incoming) is not None:
            try:
                return _merge_list_ops(existing_list, incoming, dedupe_key=_item_key)
            except ValueError as exc:
                raise OverlayTargetError(str(exc)) from exc
        if not isinstance(incoming, list):
            raise OverlayTargetError(
                "named_list_union patch must be a raw list or use '$op' (replace/add/remove/clear)"
            )
        result = [deepcopy(item) for item in existing_list]
        index_by_key = {_item_key(item): i for i, item in enumerate(result) if _item_key(item)}
        for item in incoming:
            copied = deepcopy(item)
            key = _item_key(copied)
            if key and key in index_by_key:
                result[index_by_key[key]] = copied
            else:
                result.append(copied)
                if key:
                    index_by_key[key] = len(result) - 1
        return result

    if strategy == "named_list_merge":
        identity = str(meta.get("identity") or "id")
        existing_list = list(existing or []) if isinstance(existing, list) else []

        def _merge_key(item: Any) -> str:
            if isinstance(item, dict):
                if identity == "id":
                    return str(item.get("id") or item.get("model") or "main")
                return str(item.get(identity) or item.get("id") or item.get("model") or "")
            return str(item)

        def _has_explicit_identity(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            key = "id" if identity == "id" else identity
            return bool(item.get(key))

        if _ops_dict(incoming) is not None:
            try:
                return _merge_list_ops(existing_list, incoming, dedupe_key=_merge_key)
            except ValueError as exc:
                raise OverlayTargetError(str(exc)) from exc
        if not isinstance(incoming, list):
            raise OverlayTargetError(
                "named_list_merge patch must be a raw list or use '$op' (replace/add/remove/clear)"
            )
        result = [deepcopy(item) for item in existing_list]
        index_by_key = {_merge_key(item): i for i, item in enumerate(result) if _merge_key(item)}
        for pos, item in enumerate(incoming):
            copied = deepcopy(item)
            key = _merge_key(copied)
            target_index = index_by_key.get(key) if key else None
            if (
                target_index is None
                and not _has_explicit_identity(copied)
                and pos < len(result)
                and len(existing_list) == 1
            ):
                # No explicit identity on the patch item and exactly one base
                # row — fall back to matching that row instead of appending a
                # duplicate (the derived key, e.g. from "model", may not match
                # the base row's own identity, such as a custom "id"). With 2+
                # base rows position carries no meaning, so an unmatched key
                # is treated as a genuine new addition instead of a guess.
                target_index = pos
            if target_index is not None:
                current = result[target_index]
                if isinstance(current, dict) and isinstance(copied, dict):
                    result[target_index] = apply_merge_patch(deepcopy(current), copied)
                else:
                    result[target_index] = copied
                if key:
                    index_by_key[key] = target_index
            else:
                result.append(copied)
                if key:
                    index_by_key[key] = len(result) - 1
        return result

    if strategy == "plugin_list_ops":
        existing_list = list(existing or []) if isinstance(existing, list) else []
        return _merge_plugin_list_ops(existing_list, incoming)

    if strategy == "mapping_ops":
        existing_map = existing if isinstance(existing, dict) else {}
        return _merge_mapping_ops(existing_map, incoming)

    if strategy == "plugin_binding_merge":
        return _merge_plugin_binding(existing, incoming)

    if strategy == "mapping_merge_or_ops":
        existing_map = existing if isinstance(existing, dict) else {}
        if isinstance(incoming, dict) and _ops_dict(incoming) is not None:
            return _merge_mapping_ops(existing_map, incoming)
        if isinstance(incoming, dict) and isinstance(existing_map, dict):
            merged = deepcopy(existing_map)
            merged.update(incoming)
            return merged
        return deepcopy(incoming)

    if strategy == "context_merge":
        if isinstance(incoming, dict):
            return merge_context_map(existing, incoming)
        return deepcopy(incoming)

    if strategy == "execution_merge":
        base_block = existing if isinstance(existing, dict) else {}
        ov_block = incoming or {}
        if isinstance(ov_block, dict):
            merged = deepcopy(base_block)
            for k, v in ov_block.items():
                if v is None:
                    merged.pop(k, None)
                elif k == "policies" and isinstance(v, list):
                    merged["policies"] = list(v)
                elif isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            return merged
        return deepcopy(ov_block)

    if strategy == "replace":
        return deepcopy(incoming)

    return deepcopy(incoming)


def _plugin_binding_object(raw: Any) -> dict[str, Any]:
    return deepcopy(normalize_plugin_binding(raw, field="plugin binding"))


def _deep_merge_maps(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Recurse dict values so a partial nested patch keeps sibling keys."""
    merged = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge_maps(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _merge_plugin_binding(existing: Any, incoming: Any) -> Any:
    """Merge a singleton plugin slot (string shorthand or {type, ref, params}).

    A type/ref change replaces the binding (no leftover constructor kwargs).
    Same type, or params-only overlay, deep-merges nested ``params``/``config``.
    """
    if isinstance(incoming, str) and incoming.strip():
        return normalize_plugin_binding(incoming, field="plugin binding")
    base = _plugin_binding_object(existing)
    if not isinstance(incoming, dict):
        return deepcopy(incoming)
    if _ops_dict(incoming) is not None:
        return _merge_mapping_ops(base, incoming)
    incoming_id = plugin_binding_id(incoming, field="plugin binding")
    base_id = plugin_binding_id(base, field="plugin binding")
    if incoming_id and base_id and incoming_id != base_id:
        return deepcopy(incoming)
    merged = deepcopy(base)
    for key, value in incoming.items():
        if (
            key in {"params", "config"}
            and isinstance(merged.get(key), dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge_maps(merged[key], value)
        elif isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = list(merged[key]) + list(value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _agency_entry_key(entry: dict[str, Any]) -> str | None:
    key = entry.get("id") or entry.get("name")
    if key is None:
        return None
    text = str(key).strip()
    return text or None


def apply_merge_patch(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            target[key] = apply_merge_patch(target.get(key, {}), value)
        else:
            target[key] = value
    return target


def _merge_identity_list(existing: list[Any], incoming: list[Any]) -> list[Any]:
    from mas.ctl.overrides.model import identity_pair

    result = [deepcopy(item) for item in existing]
    index: dict[tuple[str, Any], int] = {}
    for position, item in enumerate(result):
        ident = identity_pair(item)
        if ident is not None:
            index[ident] = position
    for item in incoming:
        copied = deepcopy(item)
        ident = identity_pair(copied)
        if ident is not None and ident in index:
            current = result[index[ident]]
            if isinstance(current, dict) and isinstance(copied, dict):
                result[index[ident]] = apply_merge_patch(deepcopy(current), copied)
            else:
                result[index[ident]] = copied
        else:
            result.append(copied)
            if ident is not None:
                index[ident] = len(result) - 1
    return result


def apply_identity_merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """RFC 7396, except identity lists are hierarchy (same as CLI ``[id=…]``)."""
    from mas.ctl.overrides.model import identity_pair

    merged = deepcopy(target)
    for key, value in patch.items():
        if _is_extension_key(key):
            continue
        existing = merged.get(key)
        if value is None:
            merged.pop(key, None)
            continue
        if (
            isinstance(value, list)
            and value
            and all(identity_pair(item) is not None for item in value)
        ):
            merged[key] = _merge_identity_list(existing if isinstance(existing, list) else [], value)
        elif isinstance(value, dict) and _ops_dict(value) is None and isinstance(existing, dict):
            merged[key] = apply_identity_merge_patch(existing, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _is_extension_key(key: Any) -> bool:
    return str(key).startswith("x-")


def _extension_fields(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    return {k: deepcopy(v) for k, v in doc.items() if _is_extension_key(k)}


def apply_document_extensions(target: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Copy overlay document-level ``x-*`` fields onto the composed target root.

    Extension keys live on the overlay document root (and may also appear under
    ``spec.patch``). Runtime merge of spec fields ignores these keys; they must
    land on the target document root so they match MAS/Agent schemas, not
    ``spec``. Later overlays win; nested dicts are RFC 7396-merged. A null
    value deletes the key.
    """
    patch = (overlay.get("spec") or {}).get("patch") if isinstance(overlay.get("spec"), dict) else None
    for src in (_extension_fields(overlay), _extension_fields(patch)):
        for key, value in src.items():
            existing = target.get(key)
            if value is None:
                target.pop(key, None)
            elif isinstance(existing, dict) and isinstance(value, dict):
                target[key] = apply_merge_patch(deepcopy(existing), deepcopy(value))
            else:
                target[key] = deepcopy(value)
    return target


class OverlayTargetError(ValueError):
    """Overlay target kind, name, or patch field does not match the base document."""


# Form A agency rows are `{id, ref}` (optional `name`). Agent fields from a
# MAS overlay are extracted and merged onto loaded Agent YAML — they never
# live on the MAS document.
_FORM_A_ROW_KEYS = frozenset({"id", "ref", "name"})
_MAS_KINDS = frozenset({"mas", "app", "workflow"})
_LOADED_AGENT_OVERRIDES = "__mas_cli_overrides__"


def _is_ref_based_agency_row(row: dict[str, Any]) -> bool:
    return "ref" in row and str(row.get("kind") or "").lower() != "agent"


def _strip_form_a_extras(row: dict[str, Any]) -> None:
    extra = [k for k in list(row) if k not in _FORM_A_ROW_KEYS]
    for key in extra:
        row.pop(key, None)


def _overlay_patch_body(overlay: dict[str, Any]) -> dict[str, Any]:
    overlay_spec = overlay.get("spec") or {}
    if isinstance(overlay_spec.get("patch"), dict):
        return overlay_spec["patch"]
    if isinstance(overlay_spec, dict):
        return {k: v for k, v in overlay_spec.items() if k not in {"target", "patch"}}
    return {}


def _raise_if_legacy_transport(per_agent: dict[str, Any]) -> None:
    legacy = {"agent_comm", "agent_transport", "expose"} & per_agent.keys()
    if legacy:
        raise OverlayTargetError(
            "MAS agency overlays cannot set transport or exposure fields "
            f"{sorted(legacy)!r}; use an infra Application endpoint "
            "for remote peers and `mas-ctl serve` for inbound exposure"
        )


def extract_mas_agent_patches(
    overlay: dict[str, Any],
    mas_spec: dict[str, Any],
    *,
    ref_based_only: bool = True,
) -> dict[str, Any]:
    """Per-agent overlay patches to merge onto loaded Agent YAML.

    ``patch.agents.$entry`` (and ``$all`` / ``$not-entry`` / ``$delegates``)
    expand against *mas_spec* after that overlay's ``workflow`` patch, so
    ``$entry`` follows this overlay. Form A rows stay ``{id, ref}``; the
    returned map is applied later with :func:`merge_agent_overlay`.
    """
    overlay_agents = _overlay_patch_body(overlay).get("agents")
    if not isinstance(overlay_agents, dict) or _ops_dict(overlay_agents) is not None:
        return {}
    had_entry_key = _AGENT_ALIAS_ENTRY in overlay_agents
    resolved = _resolve_mas_agent_patches(overlay_agents, mas_spec)
    agency = mas_spec.get("agency") if isinstance(mas_spec.get("agency"), dict) else {}
    agents_list = list(agency.get("agents") or [])
    by_id = {
        str(a.get("id") or a.get("name")): a
        for a in agents_list
        if isinstance(a, dict) and (a.get("id") or a.get("name"))
    }
    entry_id = _workflow_entry(mas_spec)
    out: dict[str, Any] = {}
    for agent_id, per_agent in resolved.items():
        if not isinstance(per_agent, dict):
            continue
        aid = str(agent_id)
        target = by_id.get(aid)
        if target is None:
            if had_entry_key and aid == entry_id:
                raise OverlayTargetError(
                    f"patch.agents.$entry resolved to {aid!r}, which is not in spec.agency.agents"
                )
            continue
        _raise_if_legacy_transport(per_agent)
        incoming = {k: v for k, v in per_agent.items() if k != "ref"}
        if not incoming:
            continue
        if ref_based_only and not _is_ref_based_agency_row(target):
            continue
        out[aid] = deepcopy(incoming)
    return out


def extract_agent_overlay_fanout(overlay: dict[str, Any], mas: dict[str, Any]) -> dict[str, Any]:
    """Patches from a ``target.kind: Agent`` overlay applied to a MAS.

    Form B (inline Agent) rows are merged in :func:`fanout_agent_overlay`.
    Form A rows stay ``{id, ref}``; this map is applied when those YAML
    files load. Zero matches is an error — the overlay must attach somewhere.
    """
    spec = mas.get("spec") if isinstance(mas.get("spec"), dict) else mas
    rows = _nested_agent_rows(spec if isinstance(spec, dict) else {})
    target_name = _overlay_target_name(overlay)
    raw_patch = _overlay_patch_body(overlay)
    inline_overrides = list((overlay.get("spec") or {}).get("overrides") or [])
    matched = 0
    out: dict[str, Any] = {}
    for row in rows:
        if target_name and not _row_matches_agent_target(row, target_name):
            continue
        matched += 1
        if _is_inline_agent_row(row):
            continue
        aid = str(row.get("id") or row.get("name") or "")
        if aid and (raw_patch or inline_overrides):
            out[aid] = deepcopy(raw_patch)
            if inline_overrides:
                out[aid][_LOADED_AGENT_OVERRIDES] = inline_overrides
    if matched == 0:
        named = f" named {target_name!r}" if target_name else ""
        raise OverlayTargetError(
            f"target.kind: Agent overlay{named} matched no agents in "
            "spec.agency.agents / spec.agents"
        )
    return out


def loaded_agent_patches(overlay: dict[str, Any], mas: dict[str, Any]) -> dict[str, Any]:
    """Patches to apply to separately loaded Agent YAML after this overlay merge.

    Call after :func:`merge_overlay` so ``$entry`` follows this overlay's
    workflow. The MAS document itself is unchanged.
    """
    spec = overlay.get("spec") or {}
    target = spec.get("target") if isinstance(spec.get("target"), dict) else {}
    overlay_kind = str(target.get("kind") or "").strip().lower()
    kind = str(mas.get("kind") or "").strip().lower()
    if overlay_kind in _MAS_KINDS:
        mas_spec = mas.get("spec") if isinstance(mas.get("spec"), dict) else mas
        return extract_mas_agent_patches(overlay, mas_spec if isinstance(mas_spec, dict) else {})
    if overlay_kind == "agent" and (kind in _MAS_KINDS or _base_is_mas(mas)):
        return extract_agent_overlay_fanout(overlay, mas)
    return {}


def accumulate_agent_patches(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Stack per-agent overlay patches; keep ``$op`` intact until Agent YAML loads."""
    out: dict[str, Any] = dict(existing or {})
    for agent_id, patch in (incoming or {}).items():
        if not agent_id or not isinstance(patch, dict) or not patch:
            continue
        aid = str(agent_id)
        existing_patch = out.get(aid, {})
        incoming_body = {key: value for key, value in patch.items() if key != _LOADED_AGENT_OVERRIDES}
        if incoming_body:
            existing_body = {
                key: value for key, value in existing_patch.items() if key != _LOADED_AGENT_OVERRIDES
            } if isinstance(existing_patch, dict) else {}
            out[aid] = _keep_unresolved_ops(existing_body, incoming_body)
        elif aid not in out:
            out[aid] = {}
        if _LOADED_AGENT_OVERRIDES in patch:
            if not isinstance(out.get(aid), dict):
                out[aid] = {}
            prior = out[aid].get(_LOADED_AGENT_OVERRIDES, [])
            out[aid][_LOADED_AGENT_OVERRIDES] = list(prior) + list(patch[_LOADED_AGENT_OVERRIDES])
    return out


def _compose_list_op_patches(left: Any, right: Any) -> dict[str, Any]:
    """Stack two `$op` collection patches (add lists concatenate)."""
    a = _ops_dict(left) or {}
    b = _ops_dict(right) or {}
    out: dict[str, Any] = {}
    if a.get("clear") or b.get("clear"):
        out["clear"] = True
    if "replace" in b:
        out["replace"] = list(b.get("replace") or [])
    elif "replace" in a:
        out["replace"] = list(a.get("replace") or [])
    added: list[Any] = []
    if "replace" not in b:
        added.extend(list(a.get("add") or []))
    added.extend(list(b.get("add") or []))
    if added:
        out["add"] = added
    removed: list[Any] = []
    if "replace" not in b:
        removed.extend(list(a.get("remove") or []))
    removed.extend(list(b.get("remove") or []))
    if removed:
        out["remove"] = removed
    return {"$op": out}


def _keep_unresolved_ops(existing: Any, incoming: Any) -> Any:
    """Park `$op` until the Agent YAML is loaded, when two aliases stack onto
    the same agency id (see _resolve_mas_agent_patches._layer).

    Resolving `$op.add` against an empty agent spec would collapse it to a
    short list; the later per-agent overlay merge (once that Agent's own
    document is loaded) needs the `$op` wrapper intact to compose correctly.
    Nested maps (e.g. context.role) are walked the same way.
    """
    if _ops_dict(incoming) is not None:
        if _ops_dict(existing) is not None:
            return _compose_list_op_patches(existing, incoming)
        if existing in (None, [], {}):
            return deepcopy(incoming)
        if isinstance(existing, list):
            return _merge_list_ops(existing, incoming)
        return deepcopy(incoming)
    if isinstance(incoming, dict):
        base = existing if isinstance(existing, dict) else {}
        out = deepcopy(base)
        for key, value in incoming.items():
            out[key] = _keep_unresolved_ops(base.get(key), value)
        return out
    return deepcopy(incoming)


_AGENT_ALIAS_ENTRY = "$entry"
_AGENT_ALIAS_ALL = "$all"
_AGENT_ALIAS_NOT_ENTRY = "$not-entry"
_AGENT_ALIAS_DELEGATES = "$delegates"
_AGENT_ALIASES = frozenset(
    {
        _AGENT_ALIAS_ENTRY,
        _AGENT_ALIAS_ALL,
        _AGENT_ALIAS_NOT_ENTRY,
        _AGENT_ALIAS_DELEGATES,
    }
)


def _workflow_entry(spec: dict[str, Any]) -> str:
    wf = spec.get("workflow")
    if not isinstance(wf, dict):
        return ""
    entry = wf.get("entry")
    return str(entry).strip() if entry is not None else ""


def _agency_ids(spec: dict[str, Any]) -> list[str]:
    agency = spec.get("agency")
    if not isinstance(agency, dict):
        return []
    ids: list[str] = []
    for item in agency.get("agents") or []:
        if not isinstance(item, dict):
            continue
        key = _agency_entry_key(item)
        if key:
            ids.append(key)
    return ids


def _workflow_delegates(spec: dict[str, Any], *, entry: str) -> list[str]:
    wf = spec.get("workflow")
    if not isinstance(wf, dict):
        return []
    for node in wf.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "").strip() != entry:
            continue
        return [str(item).strip() for item in (node.get("delegates_to") or []) if str(item).strip()]
    return []


def _require_workflow_entry(alias: str, entry: str) -> str:
    if entry:
        return entry
    raise OverlayTargetError(f"patch.agents.{alias} requires spec.workflow.entry on the merged MAS")


def _resolve_mas_agent_patches(overlay_agents: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Expand ``$entry`` / ``$all`` / ``$not-entry`` / ``$delegates`` onto agency ids.

    Specificity (later wins, patches compose with ``$op``): ``$all``, then
    ``$not-entry``, then ``$delegates``, then ``$entry``, then a named id.
    ``$entry`` plus that same id as a key is an error.
    """
    if not any(key in overlay_agents for key in _AGENT_ALIASES):
        return overlay_agents

    entry = _workflow_entry(spec)
    agency_ids = _agency_ids(spec)
    named = {key: value for key, value in overlay_agents.items() if key not in _AGENT_ALIASES}
    if _AGENT_ALIAS_ENTRY in overlay_agents:
        entry = _require_workflow_entry(_AGENT_ALIAS_ENTRY, entry)
        if entry in named:
            raise OverlayTargetError(
                f"patch.agents.$entry and patch.agents[{entry!r}] both set; "
                "use $entry alone — it already names the workflow entry agent"
            )
    if _AGENT_ALIAS_NOT_ENTRY in overlay_agents:
        entry = _require_workflow_entry(_AGENT_ALIAS_NOT_ENTRY, entry)

    expanded: dict[str, Any] = {}

    def _layer(agent_id: str, patch: Any) -> None:
        if not isinstance(patch, dict):
            return
        existing = expanded.get(agent_id)
        expanded[agent_id] = patch if existing is None else _keep_unresolved_ops(existing, patch)

    if _AGENT_ALIAS_ALL in overlay_agents:
        all_patch = overlay_agents[_AGENT_ALIAS_ALL]
        for agent_id in agency_ids:
            _layer(agent_id, all_patch)
    if _AGENT_ALIAS_NOT_ENTRY in overlay_agents:
        not_entry_patch = overlay_agents[_AGENT_ALIAS_NOT_ENTRY]
        for agent_id in agency_ids:
            if agent_id != entry:
                _layer(agent_id, not_entry_patch)
    if _AGENT_ALIAS_DELEGATES in overlay_agents:
        delegates_patch = overlay_agents[_AGENT_ALIAS_DELEGATES]
        for agent_id in _workflow_delegates(spec, entry=entry or _workflow_entry(spec)):
            _layer(agent_id, delegates_patch)
    if _AGENT_ALIAS_ENTRY in overlay_agents:
        _layer(entry, overlay_agents[_AGENT_ALIAS_ENTRY])
    for agent_id, patch in named.items():
        _layer(str(agent_id), patch)
    return expanded


def merge_flavour_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge a ``target.kind: Flavour`` overlay patch into a Flavour manifest.

    Deliberately narrow: only the surviving Flavour deployment-posture keys
    (see ``_FLAVOUR_PATCH_KEYS``) may be patched. ``observability``/``control``
    use the same plugin-list merge as agent overlays (:func:`_merge_plugin_list_field`)
    since the field shape — not the manifest kind — determines the semantics.
    """
    merged = deepcopy(base)
    if "spec" not in overlay:
        return merged

    overlay_spec = overlay["spec"]
    if "patch" in overlay_spec and isinstance(overlay_spec["patch"], dict):
        overlay_spec = overlay_spec["patch"]

    allowed_flavour_keys = _schema_property_keys("Flavour")
    unknown = {k for k in overlay_spec if not _is_extension_key(k)} - allowed_flavour_keys
    if unknown:
        raise OverlayTargetError(
            f"overlay patch for target.kind: Flavour contains non-deployment-posture "
            f"key(s) {sorted(unknown)!r} — see docs/design/flavour-boundary.md"
        )

    base_spec = merged.setdefault("spec", {})
    flavour_meta = _overlay_merge_meta("Flavour")

    for key, ov_val in overlay_spec.items():
        if _is_extension_key(key):
            continue
        meta = flavour_meta.get(key)
        if meta is None:
            if _ops_dict(ov_val) is not None:
                raise OverlayTargetError(f"$op is not valid for Flavour field {key!r}")
            base_spec[key] = deepcopy(ov_val)
            continue
        base_spec[key] = _merge_value_by_meta(base_spec.get(key), ov_val, meta)

    return merged


def merge_agent_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    if "spec" not in overlay:
        return merged

    overlay_spec = overlay["spec"]
    if "patch" in overlay_spec and isinstance(overlay_spec["patch"], dict):
        overlay_spec = overlay_spec["patch"]
    base_spec = merged.setdefault("spec", {})
    agent_meta = _overlay_merge_meta("Agent")

    for key, incoming in overlay_spec.items():
        if _is_extension_key(key):
            continue
        meta = agent_meta.get(key)
        if meta is not None:
            base_spec[key] = _merge_value_by_meta(base_spec.get(key), incoming, meta)
            continue
        if _ops_dict(incoming) is not None:
            raise OverlayTargetError(f"$op is not valid for Agent field {key!r}")

        if isinstance(incoming, dict) and isinstance(base_spec.get(key), dict):
            merged_map = deepcopy(base_spec.get(key) or {})
            merged_map.update(incoming)
            base_spec[key] = merged_map
        else:
            base_spec[key] = deepcopy(incoming)

    return merged


def merge_mas_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge MAS/App/Workflow overlay patch into base manifest."""
    merged = deepcopy(base)
    overlay_spec = overlay.get("spec") or {}
    patch = overlay_spec.get("patch") if isinstance(overlay_spec.get("patch"), dict) else overlay_spec
    if not isinstance(patch, dict) or not patch:
        return merged

    base_spec = merged.setdefault("spec", {})
    mas_meta = _overlay_merge_meta("MAS")

    special_keys = {"agents"}
    for key, value in patch.items():
        if key in special_keys or _is_extension_key(key):
            continue
        meta = mas_meta.get(key)
        if meta is not None:
            base_spec[key] = _merge_value_by_meta(base_spec.get(key), value, meta)
        elif _ops_dict(value) is not None:
            raise OverlayTargetError(f"$op is not valid for MAS field {key!r}")
        else:
            if isinstance(value, dict) and isinstance(base_spec.get(key), dict):
                base_spec[key] = apply_merge_patch(deepcopy(base_spec[key]), value)
            else:
                base_spec[key] = deepcopy(value)

    overlay_agents = patch.get("agents")
    if isinstance(overlay_agents, dict) and _ops_dict(overlay_agents) is not None:
        ops = _ops_dict(overlay_agents) or {}
        agency = base_spec.setdefault("agency", {})
        existing_agents = list(agency.get("agents") or [])
        if ops.get("clear") is True:
            existing_agents = []
        if "replace" in ops:
            existing_agents = list(deepcopy(ops.get("replace") or []))
        if "remove" in ops:
            rm = {str(x) for x in list(ops.get("remove") or [])}
            existing_agents = [a for a in existing_agents if not (isinstance(a, dict) and _agency_entry_key(a) in rm)]
        if "add" in ops:
            existing_keys = {
                _agency_entry_key(a)
                for a in existing_agents
                if isinstance(a, dict) and _agency_entry_key(a) is not None
            }
            for entry in list(ops.get("add") or []):
                if not isinstance(entry, dict):
                    continue
                key = _agency_entry_key(entry)
                if key is not None and key not in existing_keys:
                    existing_agents.append(deepcopy(entry))
                    existing_keys.add(key)
        agency["agents"] = existing_agents
        base_spec["agency"] = agency
    elif isinstance(overlay_agents, dict):
        # Validate aliases / unknown $entry. Form A patches are returned to
        # the caller (compile / compose) — they are not stored on this MAS.
        extract_mas_agent_patches(overlay, base_spec)
        overlay_agents = _resolve_mas_agent_patches(overlay_agents, base_spec)
        agency = base_spec.setdefault("agency", {})
        agents_list = list(agency.get("agents") or [])
        by_id = {
            str(a.get("id") or a.get("name")): a
            for a in agents_list
            if isinstance(a, dict) and (a.get("id") or a.get("name"))
        }
        for agent_id, per_agent in overlay_agents.items():
            if not isinstance(per_agent, dict):
                continue
            target = by_id.get(str(agent_id))
            if target is None:
                continue
            if "ref" in per_agent:
                target["ref"] = deepcopy(per_agent["ref"])
            _raise_if_legacy_transport(per_agent)
            incoming_patch = {k: v for k, v in per_agent.items() if k != "ref"}
            if _is_ref_based_agency_row(target):
                _strip_form_a_extras(target)
            elif incoming_patch:
                agent_spec = target.setdefault("spec", {})
                per_agent_overlay = {"spec": {"patch": deepcopy(incoming_patch)}}
                merged_agent = merge_agent_overlay({"spec": deepcopy(agent_spec)}, per_agent_overlay)
                agent_spec.clear()
                agent_spec.update(merged_agent.get("spec", {}))

        for row in agents_list:
            if isinstance(row, dict) and _is_ref_based_agency_row(row):
                _strip_form_a_extras(row)

    return merged


def _nested_agent_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Agency rows that an Agent overlay can fan out onto.

    ``spec.agency.agents`` wins when present (even if empty); otherwise
    ``spec.agents``. Empty agency.agents does not fall through to spec.agents —
    that list is the declared participant set.
    """
    agency = spec.get("agency")
    if isinstance(agency, dict) and "agents" in agency:
        agents = agency.get("agents") or []
        return [e for e in agents if isinstance(e, dict)]
    agents = spec.get("agents")
    if isinstance(agents, list):
        return [e for e in agents if isinstance(e, dict)]
    return []


def _base_is_mas(base: dict[str, Any]) -> bool:
    kind = str(base.get("kind") or "").lower()
    if kind in ("mas", "app", "workflow"):
        return True
    spec = base.get("spec") or {}
    return bool(_nested_agent_rows(spec if isinstance(spec, dict) else {}))


def _overlay_target_name(overlay: dict[str, Any]) -> str | None:
    name = ((overlay.get("spec") or {}).get("target") or {}).get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _row_matches_agent_target(row: dict[str, Any], target_name: str) -> bool:
    keys = {
        str(row.get("id") or "").strip(),
        str(row.get("name") or "").strip(),
        str((row.get("metadata") or {}).get("name") or "").strip(),
    }
    keys.discard("")
    return target_name in keys


def _is_inline_agent_row(row: dict[str, Any]) -> bool:
    return str(row.get("kind") or "").lower() == "agent" and isinstance(row.get("spec"), dict)


def fanout_agent_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Apply a ``target.kind: Agent`` overlay to inline (Form B) MAS agency rows.

    Form A rows stay ``{id, ref}``. Callers that load those Agent files
    (``compile``, ``compose`` / ``run-mas``) apply :func:`extract_agent_overlay_fanout`
    onto the YAML. This function still validates that the overlay matches at
    least one agency row.

    ``spec.target.name``, when set, selects one row (id, name, or
    metadata.name). Use the agency row id, which should match the agent
    YAML ``metadata.name`` when the overlay is reused on both. Omit the
    name to patch every nested agent. Zero matches is an error — an Agent
    overlay that attaches nowhere would look applied.
    """
    merged = deepcopy(base)
    spec = merged.setdefault("spec", {})
    if not isinstance(spec, dict):
        spec = {}
        merged["spec"] = spec
    extract_agent_overlay_fanout(overlay, merged)
    rows = _nested_agent_rows(spec)
    target_name = _overlay_target_name(overlay)
    for row in rows:
        if target_name and not _row_matches_agent_target(row, target_name):
            continue
        if _is_inline_agent_row(row):
            updated = merge_agent_overlay(row, overlay)
            row.clear()
            row.update(updated)
        elif _is_ref_based_agency_row(row):
            _strip_form_a_extras(row)
    return merged


def fanout_agent_overrides(
    base: dict[str, Any], overrides: tuple[str, ...], target_name: str | None
) -> dict[str, Any]:
    """Apply root-qualified Agent overrides to selected MAS agency rows."""
    merged = deepcopy(base)
    spec = merged.setdefault("spec", {})
    rows = _nested_agent_rows(spec if isinstance(spec, dict) else {})
    matched = 0
    from mas.ctl.overrides import apply_cli_overrides

    for row in rows:
        if target_name and not _row_matches_agent_target(row, target_name):
            continue
        if _is_inline_agent_row(row):
            updated = apply_cli_overrides(row, overrides, root="agent")
            row.clear()
            row.update(updated)
        matched += 1
    if matched == 0:
        named = f" named {target_name!r}" if target_name else ""
        raise OverlayTargetError(
            f"target.kind: Agent override{named} matched no agents in "
            "spec.agency.agents / spec.agents"
        )
    return merged


def merge_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge an Agent, MAS, Flavour, or Infra patch overlay into a base manifest.

    Dispatch is strict by canonical ``spec.target.kind``: ``MAS`` ->
    :func:`merge_mas_overlay`, ``Flavour`` -> :func:`merge_flavour_overlay`,
    ``Agent`` on an Agent document -> :func:`merge_agent_overlay`, ``Agent``
    on a MAS -> :func:`fanout_agent_overlay`.
    """
    from mas.ctl.overlay.normalize import normalize_overlay
    from mas.ctl.overrides import apply_cli_overrides

    if "spec" not in overlay:
        base_kind = str(base.get("kind", "")).lower()
        if base_kind in ("mas", "app", "workflow"):
            merged = merge_mas_overlay(base, overlay)
        elif base_kind == "flavour":
            merged = merge_flavour_overlay(base, overlay)
        else:
            merged = merge_agent_overlay(base, overlay)
        return apply_document_extensions(merged, overlay)

    spec = overlay.get("spec") or {}
    canonical = (
        overlay.get("apiVersion") == "mas/v1"
        and overlay.get("kind") == "Overlay"
        and isinstance((spec.get("target") or {}).get("kind"), str)
        and isinstance(spec.get("patch"), dict)
        and isinstance(spec.get("target"), dict)
        and bool((spec.get("target") or {}).get("kind"))
    )
    if not canonical:
        overlay = normalize_overlay(overlay, name=str((overlay.get("metadata") or {}).get("name") or "overlay"))

    overlay = deepcopy(overlay)
    overlay_spec = overlay.get("spec") or {}
    inline_overrides = list(overlay_spec.pop("overrides", []) or [])
    overlay["spec"] = overlay_spec
    _validate_patch_fields_against_target_schema(overlay, base)

    target_kind = str((overlay.get("spec") or {}).get("target", {}).get("kind", "")).lower()
    if target_kind in ("mas", "app", "workflow"):
        merged = merge_mas_overlay(base, overlay)
    elif target_kind == "flavour":
        merged = merge_flavour_overlay(base, overlay)
    elif target_kind == "infra":
        merged = deepcopy(base)
        patch = deepcopy((overlay.get("spec") or {}).get("patch") or {})
        if isinstance(patch, dict):
            merged["spec"] = apply_identity_merge_patch(
                merged.get("spec") if isinstance(merged.get("spec"), dict) else {},
                patch,
            )
    elif target_kind in ("experiment", "workspace"):
        merged = apply_merge_patch(deepcopy(base), deepcopy((overlay.get("spec") or {}).get("patch") or {}))
    elif target_kind == "agent":
        if _base_is_mas(base):
            merged = fanout_agent_overlay(base, overlay)
        else:
            merged = merge_agent_overlay(base, overlay)
    else:
        raise OverlayTargetError(
            "overlay spec.target.kind must be one of Agent, MAS, Flavour, Infra, Experiment, Workspace"
        )
    if inline_overrides:
        if target_kind == "agent" and _base_is_mas(merged):
            merged = fanout_agent_overrides(
                merged,
                tuple(inline_overrides),
                _overlay_target_name(overlay),
            )
        else:
            merged = apply_cli_overrides(merged, tuple(inline_overrides), root=target_kind)
    return apply_document_extensions(merged, overlay)
