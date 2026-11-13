#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Top-level helper: parse a raw agent spec dict into runtime-ready objects."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from mas.runtime.spec.checkpoint import parse_checkpoint_policy
from mas.runtime.spec.gov import GovernanceBinding, build_kernel_config, parse_gov_spec
from mas.runtime.spec.obs import parse_obs_spec
from mas.runtime.spec.plugin_binding import plugin_binding_id, plugin_binding_params

if TYPE_CHECKING:
    from mas.runtime.boundary.obs.binding import ObservabilityBinding
    from mas.runtime.kernel.config import KernelConfig


def _resolve_pattern_plugin_id(spec: dict[str, Any]) -> str:
    """Resolve ``spec.design_pattern`` to a pattern plugin id string."""
    from mas.runtime.agent_defaults import default_pattern_plugin_id
    from mas.runtime.registry import get_registry

    name = plugin_binding_id(spec.get("design_pattern"), field="spec.design_pattern")
    if not name:
        return default_pattern_plugin_id()

    reg = get_registry()
    info = reg.resolve_by_type("design_pattern", name)
    if info is None:
        import logging as _logging

        _logging.getLogger(__name__).warning("design_pattern %r not found in registry; passing through to kernel", name)
    return name


def _apply_design_pattern_params(spec: dict[str, Any], kernel_config: KernelConfig) -> KernelConfig:
    """``params.max_steps`` is the dispatch-loop cap (KernelConfig.max_auto_steps)."""
    params = plugin_binding_params(spec.get("design_pattern"), field="spec.design_pattern")
    updates: dict[str, Any] = {}
    if params.get("max_steps") is not None:
        updates["max_auto_steps"] = int(params["max_steps"])
    if params.get("max_cot_pass") is not None:
        updates["max_cot_pass"] = int(params["max_cot_pass"])
    if params.get("parallel") is not None:
        updates["parallel_tool_calls"] = bool(params["parallel"])
    return replace(kernel_config, **updates) if updates else kernel_config


def parse_agent_spec(
    spec: dict[str, Any],
    *,
    runtime_engine: dict[str, Any] | None = None,
    manifest_dir: Any | None = None,
    agent_id: str = "",
) -> tuple[KernelConfig, ObservabilityBinding | None]:
    """Parse a raw agent spec dict into (KernelConfig, ObservabilityBinding | None).

    ``spec`` is the inner ``spec:`` block from an agent manifest, e.g.::

        manifest["spec"]

    Returns a tuple of:
    - ``KernelConfig`` built from governance spec + design pattern
    - ``ObservabilityBinding | None`` (None when observability is absent/empty)
    """
    gov_raw = spec.get("governance")
    obs_raw = spec.get("observability")
    parse_checkpoint_policy(spec.get("checkpoint"))
    gov_binding: GovernanceBinding = parse_gov_spec(gov_raw)
    pattern_plugin_id = _resolve_pattern_plugin_id(spec)

    kernel_config = build_kernel_config(
        gov_binding,
        pattern_plugin_id=pattern_plugin_id,
        agent_spec=spec,
        agent_id=agent_id,
        manifest_dir=manifest_dir,
    )
    kernel_config = _apply_design_pattern_params(spec, kernel_config)

    from mas.runtime.spec.runtime_engine import apply_runtime_engine_to_kernel

    kernel_config = apply_runtime_engine_to_kernel(kernel_config, runtime_engine)

    obs_binding = parse_obs_spec(obs_raw)
    obs_result: ObservabilityBinding | None = obs_binding if obs_binding.plugins else None

    return kernel_config, obs_result


__all__ = ["parse_agent_spec"]
