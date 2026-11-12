#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Spot JSON Schema ``deprecated: true`` properties and removed experiment keys.

Draft-07 ignores ``deprecated``, so validators must walk the schema themselves.
Removed keys are rejected by ``additionalProperties: false``; this module
supplies replacement hints for those error messages.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mas.runtime.spec.endpoint_usage import deprecated_usage

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


def _workflow_nodes(data: dict[str, Any]) -> tuple[list[Any] | None, str]:
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


def _nonempty(raw: object) -> bool:
    if isinstance(raw, list):
        return any(str(item).strip() for item in raw)
    return bool(raw)


def _nodes_setting(data: dict[str, Any], key: str) -> tuple[str, list[str]]:
    """The workflow nodes path and the ids (in order, once each) of nodes with a non-empty ``key``."""
    nodes, path = _workflow_nodes(data)
    if not isinstance(nodes, list) or not path:
        return "", []
    labels: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not _nonempty(node.get(key)):
            continue
        label = str(node.get("id") or "").strip() or f"nodes[{index}]"
        if label not in labels:
            labels.append(label)
    return path, labels


def collect_delegates_to_deprecation(data: dict[str, Any]) -> list[tuple[str, str]]:
    """One warning per validation, listing nodes with a non-empty ``delegates_to``."""
    path, labels = _nodes_setting(data, "delegates_to")
    if not labels:
        return []
    listed = ", ".join(labels)
    return [
        (
            path,
            f"delegates_to is deprecated, still supported, no removal date; used by nodes: {listed}",
        )
    ]


def collect_dispatch_deprecation(data: dict[str, Any]) -> list[tuple[str, str]]:
    """One warning per validation, listing nodes that set ``dispatch``. The key is ignored."""
    path, labels = _nodes_setting(data, "dispatch")
    if not labels:
        return []
    listed = ", ".join(labels)
    return [
        (
            path,
            f"dispatch is deprecated and ignored; the model chooses parallel tool calls; set by nodes: {listed}",
        )
    ]


def collect_workflow_deprecations(data: dict[str, Any]) -> list[tuple[str, str]]:
    """The workflow node deprecations, one warning per deprecated key."""
    return collect_delegates_to_deprecation(data) + collect_dispatch_deprecation(data)


_FLAVOUR_IGNORED_KEYS = ("agent_comm", "tools", "telemetry", "observability")


def _flavour_block_for_deprecation(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The Flavour ``spec`` (or a Flavour overlay's ``spec.patch``) and its path."""
    if not isinstance(data, dict):
        return None, ""
    kind = str(data.get("kind") or "").strip().lower()
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return None, ""
    if kind == "flavour":
        return spec, "spec"
    if kind == "overlay":
        target = spec.get("target")
        patch = spec.get("patch")
        if (
            isinstance(target, dict)
            and str(target.get("kind") or "").strip().lower() == "flavour"
            and isinstance(patch, dict)
        ):
            return patch, "spec.patch"
    return None, ""


def collect_flavour_key_deprecation(data: dict[str, Any]) -> list[tuple[str, str]]:
    """One warning per validation, listing leftover Flavour keys that are set.

    ``agent_comm``, ``tools``, ``telemetry`` and ``observability`` are accepted
    and read by nothing. A leftover ``control`` mapping is unread; a ``control``
    list of Control refs is live.
    """
    spec, path = _flavour_block_for_deprecation(data)
    if spec is None:
        return []
    ignored = [key for key in _FLAVOUR_IGNORED_KEYS if key in spec]
    if isinstance(spec.get("control"), dict):
        ignored = [*ignored, "control"] if "control" not in ignored else ignored
    if not ignored:
        return []
    listed = ", ".join(ignored)
    verb = "is" if len(ignored) == 1 else "are"
    whom = "it" if len(ignored) == 1 else "them"
    return [(path, f"{listed} {verb} deprecated and ignored; nothing reads {whom}")]


def _usage_rows(node: Any, path: str) -> list[tuple[str, dict[str, Any]]]:
    """Every ``spec.endpoints`` / ``spec.tool_servers`` row under ``node``, with its path."""
    rows: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key == "endpoints" and isinstance(value, dict):
                rows.extend((f"{child}.{name}", row) for name, row in value.items() if isinstance(row, dict))
            elif key == "tool_servers" and isinstance(value, list):
                for i, row in enumerate(value):
                    if isinstance(row, dict):
                        label = str(row.get("id") or "").strip()
                        rows.append((f"{child}[{label or i}]", row))
            else:
                rows.extend(_usage_rows(value, child))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            rows.extend(_usage_rows(item, f"{path}[{i}]"))
    return rows


# Leftover lists a Control now owns. Still applied when no matching Control names them.
_AGENT_CONTROL_KEYS = ("observability", "governance")
# Still enforced as before; the budget governance plugin replaces it.
_AGENT_BUDGET_KEYS = ("budget",)
# ControlBinding keys no plugin reads. `retry` and `circuit_breaker` in it are still applied by the engine
# and move to the infra pipeline in the next release, so they do not warn yet.
_UNREAD_CONTROL_KEYS = ("budget", "rate_limiter")


def _unread_control_keys(control: object, prefix: str) -> list[str]:
    """``prefix.key`` for each ControlBinding key that is set and read by nothing."""
    if not isinstance(control, dict):
        return []
    merged = ((control.get("$op") or {}).get("merge") or {}) if isinstance(control.get("$op"), dict) else control
    if not isinstance(merged, dict):
        return []
    return [f"{prefix}.{key}" for key in _UNREAD_CONTROL_KEYS if key in merged]


def collect_agent_control_keys_deprecation(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Warn once when an Agent, MAS, or Agent/MAS Overlay sets a key a ``kind: Control`` owns.

    ``observability`` and ``governance`` constrain a run rather than define the agent, so they
    belong in a ``kind: Control`` document a Flavour attaches. They still apply when no matching
    Control names them. Overlay patches that set them target ``kind: Control``. ``budget`` is still
    enforced; the ``budget`` governance plugin replaces it. Agent ``spec.control`` is deprecated:
    ``retry`` and ``circuit_breaker`` in it still apply and move to the infra pipeline; ``budget`` and
    ``rate_limiter`` in it are read by nothing, and warn.
    Overlay ``spec.control`` (Control file refs) is the attach list, not a leftover.
    """
    if not isinstance(data, dict):
        return []
    kind = str(data.get("kind") or "").strip().lower()
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return []
    unread: list[str] = []
    budget: list[str] = []
    if kind in {"agent", "mas"}:
        budget = [f"spec.{key}" for key in _AGENT_BUDGET_KEYS if key in spec]
        present = [f"spec.{key}" for key in _AGENT_CONTROL_KEYS if key in spec]
        unread = _unread_control_keys(spec.get("control"), "spec.control")
        where = "spec"
    elif kind == "overlay":
        target = str(((spec.get("target") or {}) if isinstance(spec.get("target"), dict) else {}).get("kind") or "")
        if target.strip().lower() not in {"agent", "mas"}:
            return []
        patch = spec.get("patch") if isinstance(spec.get("patch"), dict) else {}
        present = [f"spec.patch.{key}" for key in _AGENT_CONTROL_KEYS if key in patch]
        unread = _unread_control_keys(patch.get("control"), "spec.patch.control")
        budget = [f"spec.patch.{key}" for key in _AGENT_BUDGET_KEYS if key in patch]
        for key in _AGENT_CONTROL_KEYS:
            if key in spec:
                present.append(f"spec.{key}")
        where = "spec.patch"
    else:
        return []
    out: list[tuple[str, str]] = []
    if present:
        verb, whom = ("is", "it") if len(present) == 1 else ("are", "them")
        out.append(
            (
                where,
                f"{', '.join(present)} {verb} deprecated and still applied when no matching Control names {whom}: "
                f"put {whom} in a kind: Control document that a Flavour attaches (spec.control)",
            )
        )
    if budget:
        out.append(
            (
                where,
                f"{budget[0]} is deprecated and still enforced: "
                "name the budget plugin in a kind: Control spec.governance instead",
            )
        )
    if unread:
        verb = "is" if len(unread) == 1 else "are"
        out.append((where, f"{', '.join(unread)} {verb} deprecated and read by no plugin: remove it"))
    return out


def collect_system_tool_kind_deprecation(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Warn for each ``{kind: system, name}`` entry of an Agent's (or an Overlay patch's) ``spec.tools``.

    The entry still grants the plugin; ``spec.system_plugins`` is its replacement.
    """
    if not isinstance(data, dict):
        return []
    spec = data.get("spec")
    if not isinstance(spec, dict):
        return []
    kind = str(data.get("kind") or "").strip().lower()
    if kind == "agent":
        tools, where = spec.get("tools"), "spec.tools"
    elif kind == "overlay":
        patch = spec.get("patch")
        tools, where = (patch.get("tools") if isinstance(patch, dict) else None), "spec.patch.tools"
        if isinstance(tools, dict):  # collection ops: {$op: {add: [...]}}
            ops = tools.get("$op")
            tools, where = (ops.get("add") if isinstance(ops, dict) else None), "spec.patch.tools.$op.add"
    else:
        return []
    if not isinstance(tools, list):
        return []
    out: list[tuple[str, str]] = []
    for index, item in enumerate(tools):
        if isinstance(item, dict) and item.get("kind") == "system":
            name = str(item.get("name") or "").strip() or "<name>"
            out.append(
                (
                    f"{where}[{index}]",
                    f"{{kind: system, name: {name}}} is deprecated and still grants the plugin: "
                    f"list {name!r} in spec.system_plugins (params become the plugin settings)",
                )
            )
    return out


def collect_endpoint_usage_deprecation(
    data: Any = None,
    *,
    rows: Iterable[tuple[str, Any]] = (),
) -> list[tuple[str, str]]:
    """One warning per endpoint or tool server row whose ``usage`` is a deprecated value.

    ``data`` is an infra document (bundles included); ``rows`` are already
    resolved ``(label, row)`` pairs, as compose has them.
    """
    found = list(rows) + (_usage_rows(data, "") if data is not None else [])
    msgs: list[tuple[str, str]] = []
    for path, row in found:
        if not isinstance(row, dict):
            continue
        usage = row.get("usage")
        replacement = deprecated_usage(usage)
        if replacement is None:
            continue
        msgs.append(
            (
                f"{path}.usage",
                f"usage: {usage} is deprecated, still supported; use usage: {replacement}",
            )
        )
    return msgs


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
