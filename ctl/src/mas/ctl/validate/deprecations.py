#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Spot JSON Schema ``deprecated: true`` properties and removed experiment keys.

Draft-07 ignores ``deprecated``, so validators must walk the schema themselves.
Removed keys are rejected by ``additionalProperties: false``; this module
supplies replacement hints for those error messages.
"""

from __future__ import annotations

from typing import Any

# Keys that used to appear on experiment: and are now schema-rejected.
#
# NOTE: "mas" is deliberately NOT included here. Unlike these keys, `mas:` is
# a soft-deprecated alias of `application:` for one more release — it stays
# schema-accepted (see docs/schemas/lab/experiment.schema.yaml's `mas:` def,
# which is `deprecated: true` but not additionalProperties-rejected) and the
# loader warns via NOTICES["experiment.mas"] rather than raising. Listing it
# here would contradict the schema's own removed-keys list (see that file's
# top-level description) and the loader's intentional soft-acceptance.
REMOVED_EXPERIMENT_KEYS = {
    "pipeline_bind": "declare hooks under run/item/scenario/post",
    "pipeline": "use run/item/scenario/post hooks (CLI --depth exp|scenario|item|run)",
    "output_dir": "remove; output paths are derived from lab layout",
    "flavours": "use default_flavour (library-standard flavours)",
    "plots": "declare plot steps in experiment-level post: or scenario.post",
}

_LEVEL_SECTION_KEYS = frozenset({"pre", "post", "artifacts", "n_runs"})
_MAS_BINDING_KEYS = frozenset({"app", "manifest", "configs_dir", "base_scenario"})


def _deref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    merged = dict(schema)
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.rsplit("/", 1)[-1]
        target = (root.get("$defs") or {}).get(name) or {}
        merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    if "allOf" in merged:
        acc: dict[str, Any] = {}
        for part in merged.get("allOf") or []:
            if isinstance(part, dict):
                acc.update(_deref(part, root))
        extra = {k: v for k, v in merged.items() if k != "allOf"}
        acc.update(extra)
        merged = acc
    return merged


def collect_deprecated_uses(
    instance: Any,
    schema: dict[str, Any] | None,
    *,
    root: dict[str, Any] | None = None,
    path: str = "",
) -> list[str]:
    """Return warning strings for instance keys marked ``deprecated: true``."""
    if not isinstance(schema, dict):
        return []
    root = root if root is not None else schema
    schema = _deref(schema, root)
    msgs: list[str] = []
    if schema.get("deprecated") and path:
        replaced = schema.get("x-replaced-by")
        hint = f"; use {replaced}" if replaced else ""
        msgs.append(f"{path} is deprecated{hint}")
    if isinstance(instance, dict):
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key not in instance:
                continue
            child_path = f"{path}.{key}" if path else str(key)
            msgs.extend(collect_deprecated_uses(instance[key], sub, root=root, path=child_path))
    elif isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                child_path = f"{path}[{i}]" if path else f"[{i}]"
                msgs.extend(collect_deprecated_uses(item, items, root=root, path=child_path))
    return msgs


def _delegates_to_nodes(data: dict[str, Any]) -> tuple[list[Any] | None, str]:
    spec = data.get("spec") if isinstance(data, dict) else None
    if not isinstance(spec, dict):
        return None, ""
    workflow = spec.get("workflow")
    if isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list):
        return workflow["nodes"], "spec.workflow.nodes"
    if isinstance(spec.get("nodes"), list):
        return spec["nodes"], "spec.nodes"
    patch = spec.get("patch")
    if isinstance(patch, dict):
        patched = patch.get("workflow")
        if isinstance(patched, dict) and isinstance(patched.get("nodes"), list):
            return patched["nodes"], "spec.patch.workflow.nodes"
    return None, ""


def _nonempty_delegates_to(raw: object) -> bool:
    if isinstance(raw, list):
        return any(str(item).strip() for item in raw)
    return bool(raw)


def collect_delegates_to_deprecation(data: dict[str, Any]) -> list[tuple[str, str]]:
    """One warning per validation, listing nodes with a non-empty ``delegates_to``."""
    nodes, path = _delegates_to_nodes(data)
    if not isinstance(nodes, list) or not path:
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or "delegates_to" not in node:
            continue
        if not _nonempty_delegates_to(node.get("delegates_to")):
            continue
        label = str(node.get("id") or "").strip() or f"nodes[{index}]"
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    if not labels:
        return []
    listed = ", ".join(labels)
    return [
        (
            path,
            f"delegates_to is deprecated, still supported, no removal date; used by nodes: {listed}",
        )
    ]


def collect_experiment_deprecations(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    """Deprecated experiment.yaml keys plus application-as-pipeline-level."""
    msgs = collect_deprecated_uses(data, schema, root=schema)
    exp = data.get("experiment")
    if not isinstance(exp, dict):
        return msgs
    app = exp.get("application")
    if isinstance(app, dict):
        keys = set(app)
        if keys & _LEVEL_SECTION_KEYS and not keys & _MAS_BINDING_KEYS:
            msgs.append(
                "experiment.application is used as a pipeline level; use experiment-level post: (CLI --depth exp)"
            )
    return msgs


def hint_for_removed_experiment_key(field: str) -> str:
    hint = REMOVED_EXPERIMENT_KEYS.get(field, "")
    if hint:
        return f"{field} was removed; {hint}"
    return ""
