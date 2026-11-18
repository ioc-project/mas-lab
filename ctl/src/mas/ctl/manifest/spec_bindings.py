#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Parse agent spec contract bindings — strict v2 shapes only.

Allowed binding keys are generated from JSON Schema
(``scripts/gen_schema_artifacts.py`` → ``mas.runtime.spec.schema_bindings_generated``).
This module adds semantic checks that schema alone does not express (integer ranges,
nested object key sets).

Cardinality-one fields (string shorthand or `{type, ref, params}`): ``design_pattern``,
``context_manager``, ``assembler``, ``llm``, ``memory``.

Multi-cardinality fields (list): ``observability`` (sequence), ``tools``,
``skills``, ``governance`` (chain — see governance-binding.schema.yaml).

Observability is a **sequence** (every plugin sees every event).
Governance is an **iptables-style chain** (BLOCK stops and returns the
error; ALLOW passes to the next plugin)::

    governance:
      - sample_governance:
          hitl_on_tool: true
          hitl_on_tool_result: true
          hitl_mode: interactive
"""

from __future__ import annotations

import os
from typing import Any

# ObservabilityBinding and GovernanceBinding now live in the runtime — import
# and re-export so all existing ctl importers continue to work unchanged.
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.engine.tools import spawn_subagent_params
from mas.runtime.spec.checkpoint import parse_checkpoint_policy
from mas.runtime.spec.gov import GovernanceBinding
from mas.runtime.spec.schema_bindings_generated import (
    ASSEMBLER_BINDING_KEYS,
    CONTEXT_MANAGER_BINDING_KEYS,
    CONTROL_BINDING_KEYS,
    DESIGN_PATTERN_BINDING_KEYS,
    EXECUTION_BINDING_KEYS,
    EXECUTION_CACHE_KEYS,
    LLM_BINDING_KEYS,
)


class SpecBindingError(ValueError):
    """Manifest spec binding shape violates v2 contract."""


def normalize_obs_plugin(name: str) -> str:
    """Return trimmed plugin id — hyphens normalized to underscores."""
    return (name or "").strip().replace("-", "_")


def resolve_manifest_cfg_value(cfg: dict[str, Any], key: str, *, default: str = "") -> str:
    """Resolve a manifest config value from inline field or ``{key}_env`` reference."""
    direct = cfg.get(key)
    if direct is not None and str(direct).strip():
        return str(direct).strip()
    env_key = cfg.get(f"{key}_env")
    if env_key:
        return os.environ.get(str(env_key), default).strip()
    return default


def resolve_path_cfg(cfg: dict[str, Any]) -> str | None:
    """Resolve first non-empty path-like field (inline or env) from plugin config."""
    for key in ("path", "output_path", "events_file", "file_export_path"):
        val = resolve_manifest_cfg_value(cfg, key)
        if val:
            return val
    return None


def _resolve_path_cfg(cfg: dict[str, Any]) -> str | None:
    return resolve_path_cfg(cfg)


def _parse_obs_list(items: list[Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    plugins: list[str] = []
    configs: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, str):
            plugins.append(normalize_obs_plugin(item))
        elif isinstance(item, dict):
            for raw_name, cfg in item.items():
                name = normalize_obs_plugin(str(raw_name))
                plugins.append(name)
                if isinstance(cfg, dict):
                    configs[name] = dict(cfg)
        else:
            raise SpecBindingError(f"observability list entries must be str or dict, got {type(item).__name__}")
    return plugins, configs


def parse_observability(raw: Any) -> ObservabilityBinding:
    """Parse ``spec.observability`` — must be a list or absent."""
    from mas.runtime.spec.obs import SpecBindingError as RuntimeObsError
    from mas.runtime.spec.obs import parse_obs_spec

    try:
        return parse_obs_spec(raw)
    except RuntimeObsError as exc:
        raise SpecBindingError(str(exc)) from exc


def parse_governance(raw: Any) -> GovernanceBinding:
    """Parse ``spec.governance`` — plugin list only (see governance-binding.schema.yaml)."""
    from mas.runtime.spec.gov import SpecBindingError as RuntimeSpecBindingError
    from mas.runtime.spec.gov import parse_gov_spec

    try:
        return parse_gov_spec(raw)
    except RuntimeSpecBindingError as exc:
        raise SpecBindingError(str(exc)) from exc


def _reject_unknown_keys(raw: dict[str, Any], *, allowed: frozenset[str], field: str) -> None:
    for key in raw:
        if key not in allowed:
            raise SpecBindingError(f"{field}: unknown field {key!r}")


def parse_design_pattern(raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, str):
        if not raw.strip():
            raise SpecBindingError("spec.design_pattern string must be a plugin name")
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"spec.design_pattern must be a plugin name or object, got {type(raw).__name__}")
    _reject_unknown_keys(raw, allowed=DESIGN_PATTERN_BINDING_KEYS, field="spec.design_pattern")


def parse_context_manager(raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, str):
        if not raw.strip():
            raise SpecBindingError("spec.context_manager string must be a plugin name")
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"spec.context_manager must be a plugin name or object, got {type(raw).__name__}")
    _reject_unknown_keys(raw, allowed=CONTEXT_MANAGER_BINDING_KEYS, field="spec.context_manager")


def parse_assembler(raw: Any) -> None:
    if raw is None:
        return
    if isinstance(raw, str):
        if not raw.strip():
            raise SpecBindingError("spec.assembler string must be a plugin name")
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"spec.assembler must be a plugin name or object, got {type(raw).__name__}")
    _reject_unknown_keys(raw, allowed=ASSEMBLER_BINDING_KEYS, field="spec.assembler")


def parse_llm(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"spec.llm must be an object, got {type(raw).__name__}")
    _reject_unknown_keys(raw, allowed=LLM_BINDING_KEYS, field="spec.llm")


def parse_contract_binding(raw: Any, *, field: str) -> None:
    """Validate one manifest-selected Protocol contract binding."""
    if raw is None:
        return
    if isinstance(raw, str):
        if not raw.strip():
            raise SpecBindingError(f"{field} must not be empty")
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"{field} must be a plugin name or object")
    _reject_unknown_keys(raw, allowed=frozenset({"type", "ref", "params"}), field=field)
    if not (str(raw.get("type") or raw.get("ref") or "").strip()):
        raise SpecBindingError(f"{field} requires type or ref")
    if "params" in raw and not isinstance(raw["params"], dict):
        raise SpecBindingError(f"{field}.params must be an object")


def parse_loop(raw: Any) -> None:
    """Validate ``spec.loop``: ``type`` names a registered ``agent_loop`` plugin, ``params`` its arguments."""
    if raw is None:
        return
    if isinstance(raw, dict):
        _reject_unknown_keys(raw, allowed=frozenset({"type", "params"}), field="spec.loop")
    parse_contract_binding(raw, field="spec.loop")
    name = (raw if isinstance(raw, str) else str(raw.get("type") or "")).strip()
    from mas.runtime.registry import get_registry

    if get_registry().resolve_by_type("agent_loop", name) is None:
        raise SpecBindingError(f"spec.loop: {name!r} is not a registered agent_loop plugin")


def parse_execution(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"spec.execution must be an object, got {type(raw).__name__}")
    _reject_unknown_keys(raw, allowed=EXECUTION_BINDING_KEYS, field="spec.execution")
    cache = raw.get("cache")
    if isinstance(cache, dict):
        _reject_unknown_keys(cache, allowed=EXECUTION_CACHE_KEYS, field="spec.execution.cache")
    depth = raw.get("engine_queue_depth")
    if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth < 1):
        raise SpecBindingError("spec.execution.engine_queue_depth must be an integer >= 1")
    max_steps = raw.get("max_auto_steps")
    if max_steps is not None and (not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1):
        raise SpecBindingError("spec.execution.max_auto_steps must be an integer >= 1")


def parse_control(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise SpecBindingError(f"spec.control must be an object, got {type(raw).__name__}")
    _reject_unknown_keys(raw, allowed=CONTROL_BINDING_KEYS, field="spec.control")
    _check_control_params(raw)


def _check_control_params(raw: dict[str, Any]) -> None:
    """Check the deprecated ``spec.control.<key>`` mappings with the layer that answers to each key.

    A layer row names its key (``control_key``); the layer class checks its own params, so ctl knows no layer.
    """
    from mas.runtime.layers.compose import layers_declaring, require_layer_class

    for name, key in layers_declaring("control_key"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise SpecBindingError(f"spec.control.{key} must be an object or null")
        layer = require_layer_class(name)
        slots = {"llm": "llm", "tools": "tool"}
        if set(value) & set(slots):
            unknown = sorted(set(value) - set(slots))
            if unknown:
                raise SpecBindingError(f"spec.control.{key}: unknown field {unknown[0]!r}")
            checks = [(slot, params, (kind,)) for slot, kind in slots.items() if (params := value.get(slot))]
        else:
            checks = [(key, {k: v for k, v in value.items() if k not in {"plugin", "enabled"}}, None)]
        for slot, params, kinds in checks:
            try:
                layer.wrap(None, dict(params), call_kinds=kinds)
            except Exception as exc:
                raise SpecBindingError(f"spec.control.{slot}: {exc}") from exc


def parse_infra_lists(raw_spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Reject deployment infrastructure from agent/MAS specs."""
    refs_raw = raw_spec.get("infra_refs") or raw_spec.get("infra_ref")
    if refs_raw:
        raise SpecBindingError("spec.infra_refs is forbidden; use workspace config or --infra-ref")
    interceptors_raw = raw_spec.get("infra_interceptors") or raw_spec.get("infra_interceptor")
    refs = _as_str_list(refs_raw, field="spec.infra_refs")
    interceptors = _as_str_list(interceptors_raw, field="spec.infra_interceptors")
    return refs, interceptors


def _as_str_list(raw: Any, *, field: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out: list[str] = []
        for i, item in enumerate(raw):
            if not isinstance(item, str) or not item.strip():
                raise SpecBindingError(f"{field}[{i}] must be a non-empty string")
            out.append(item.strip())
        return out
    raise SpecBindingError(f"{field} must be a string or list of strings")


def validate_agent_spec_bindings(spec: Any) -> None:
    """Validate shapes of present contract bindings (schema defines allowed keys)."""
    if spec is None:
        return
    if not isinstance(spec, dict):
        raise SpecBindingError(f"spec must be an object, got {type(spec).__name__}")
    if "governance" in spec:
        parse_governance(spec["governance"])
    if "observability" in spec:
        parse_observability(spec["observability"])
    if "checkpoint" in spec:
        parse_checkpoint_policy(spec["checkpoint"])
    behavior = spec.get("behavior") or {}
    if not isinstance(behavior, dict):
        raise SpecBindingError("spec.behavior must be an object")
    parse_spawn_subagent_params(spawn_subagent_params(spec))
    if "llm" in spec:
        parse_llm(spec["llm"])
    for field in ("hitl_contract", "user_io_contract"):
        if field in spec:
            parse_contract_binding(spec[field], field=f"spec.{field}")
    if "execution" in spec:
        raise SpecBindingError(
            "spec.execution is not allowed on Agent manifests — configure RuntimeEngine "
            "via workspace runtime_refs or --runtime-ref"
        )
    if "runtime_refs" in spec or "runtime_ref" in spec:
        raise SpecBindingError("spec.runtime_refs is not allowed on Agent manifests — use workspace config or CLI")
    if "infra_refs" in spec or "infra_ref" in spec:
        raise SpecBindingError("spec.infra_refs is not allowed on Agent manifests — use workspace config or CLI")
    if "infra_interceptors" in spec or "infra_interceptor" in spec:
        raise SpecBindingError("spec.infra_interceptors is not allowed on Agent manifests — use workspace config")
    if "control" in spec:
        parse_control(spec["control"])
    if "loop" in spec:
        parse_loop(spec["loop"])
    if "design_pattern" in spec:
        parse_design_pattern(spec["design_pattern"])
    if "context_manager" in spec:
        parse_context_manager(spec["context_manager"])
    if "assembler" in spec:
        parse_assembler(spec["assembler"])
    if "context_plugin" in spec:
        raise SpecBindingError("spec.context_plugin was removed; use spec.assembler")


def parse_spawn_subagent_params(raw: Any) -> dict[str, Any] | None:
    """Validate the ``spawn_subagent`` tools entry's ``params`` block.

    ``None`` means the tool is not declared, which is the only way to say
    "this agent cannot spawn" — there is no separate capability flag.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"templates", "max_spawns", "max_depth"}:
        raise SpecBindingError("spawn_subagent params must contain only templates, max_spawns, and max_depth")
    templates = parse_subagent_templates(raw.get("templates"))
    if not templates:
        raise SpecBindingError("spawn_subagent params.templates must declare at least one template")
    bounds = {"max_spawns": 8, "max_depth": 3}
    for key, default in bounds.items():
        value = raw.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SpecBindingError(f"spawn_subagent params.{key} must be an integer >= 1")
        bounds[key] = value
    return {"templates": templates, **bounds}


def parse_subagent_templates(raw: Any) -> list[dict[str, str]]:
    """Validate named, local-reference subagent templates."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecBindingError("spawn_subagent params.templates must be a list")
    templates: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) - {"id", "ref", "description"}:
            raise SpecBindingError(
                f"spawn_subagent params.templates[{index}] must contain only id, ref, and description"
            )
        template_id = entry.get("id")
        ref = entry.get("ref")
        description = entry.get("description", "")
        if not isinstance(template_id, str) or not template_id.strip():
            raise SpecBindingError(f"spawn_subagent params.templates[{index}].id must be a non-empty string")
        if not isinstance(ref, str) or not ref.strip():
            raise SpecBindingError(f"spawn_subagent params.templates[{index}].ref must be a non-empty string")
        if not isinstance(description, str):
            raise SpecBindingError(f"spawn_subagent params.templates[{index}].description must be a string")
        if template_id in seen:
            raise SpecBindingError(f"duplicate subagent template id {template_id!r}")
        seen.add(template_id)
        templates.append({"id": template_id, "ref": ref, "description": description})
    return templates


def parse_sink_from_deployment(deployment: dict | None) -> str | None:
    """Extract observability sink/backend ref from a deployment manifest."""
    if not deployment:
        return None
    spec = deployment.get("spec") or {}
    shared = spec.get("shared") or {}
    if isinstance(shared, dict):
        ref = shared.get("observability_ref") or shared.get("sink_ref")
        if ref:
            return str(ref)
    obs = spec.get("observability") or {}
    if isinstance(obs, dict):
        return obs.get("backend") or obs.get("sink_ref")
    return None
