#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pure spec parsers for agent governance — runtime-owned, no ctl dependency."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any


class SpecBindingError(ValueError):
    """Agent spec governance binding violates v2 contract."""


@dataclass(frozen=True)
class GovernanceBinding:
    """Governance plugin list + derived kernel fields."""

    plugins: list[str] = field(default_factory=list)
    plugin_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    hitl_on_tool: bool | None = None
    hitl_on_tool_result: bool | None = None
    gov_policy_profile: str | None = None
    gov_block_destructive: bool | None = None
    gov_trigger_destructive: bool | None = None
    gov_ingress_profile: str | None = None
    enable_memory_egress: bool | None = None
    enable_transport_egress: bool | None = None
    max_cot_pass: int | None = None
    max_gov_retries: int | None = None
    hitl_mode: str | None = None
    hitl_once_per_turn: bool | None = None
    policies: list[dict[str, Any]] = field(default_factory=list)
    active_profile: str | None = None
    error_recovery_plugin: str | None = None
    backtrack: dict[str, Any] | None = None
    error_policy: dict[str, Any] | None = None
    ingress_plugins: list[dict[str, Any]] = field(default_factory=list)


def plugin_priority(cfg: dict[str, Any]) -> int:
    """A plugin attachment's ``priority`` (higher first, default 0). Anything but an integer is refused."""
    raw = cfg.get("priority", 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SpecBindingError(f"priority must be an integer, got {raw!r}")
    return raw


def _parse_gov_plugin_list(
    items: list[Any],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    configs: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            configs[name] = {}
        elif isinstance(item, dict):
            for raw_name, cfg in item.items():
                name = str(raw_name).strip()
                configs[name] = dict(cfg) if isinstance(cfg, dict) else {}
        else:
            raise SpecBindingError(f"governance list entries must be str or dict, got {type(item).__name__}")
    plugins = sorted(configs, key=lambda name: (-plugin_priority(configs[name]), name))
    return plugins, configs


def parse_gov_spec(raw: list | None) -> GovernanceBinding:
    """Parse ``spec.governance`` — a set of plugins.

    Same-transition order is each attachment's ``priority`` (higher first,
    default 0; same priority is the plugin id). A later stanza for the same
    id replaces the earlier config.
    """
    if raw is None:
        return GovernanceBinding()

    if not isinstance(raw, list):
        raise SpecBindingError(
            f"spec.governance must be a plugin list, got {type(raw).__name__}. "
            "Wrap kernel fields in a plugin stanza, e.g. "
            "governance: [{sample_governance: {hitl_on_tool: true}}]"
        )

    plugins, configs = _parse_gov_plugin_list(raw)
    flat: dict[str, Any] = {}
    policies: list[dict[str, Any]] = []
    ingress_plugins: list[dict[str, Any]] = []
    for cfg in configs.values():
        for key, value in cfg.items():
            if key == "policies" and isinstance(value, list):
                policies.extend(p for p in value if isinstance(p, dict))
            elif key == "ingress_plugins" and isinstance(value, list):
                ingress_plugins.extend(p for p in value if isinstance(p, dict))
            elif key not in {"policies", "ingress_plugins", "profiles"}:
                flat.setdefault(key, value)
    return GovernanceBinding(
        plugins=plugins,
        plugin_configs=configs,
        hitl_on_tool=flat.get("hitl_on_tool"),
        hitl_on_tool_result=flat.get("hitl_on_tool_result"),
        gov_policy_profile=flat.get("gov_policy_profile"),
        gov_block_destructive=flat.get("gov_block_destructive"),
        gov_trigger_destructive=flat.get("gov_trigger_destructive"),
        gov_ingress_profile=flat.get("gov_ingress_profile"),
        enable_memory_egress=flat.get("enable_memory_egress"),
        enable_transport_egress=flat.get("enable_transport_egress"),
        max_cot_pass=flat.get("max_cot_pass"),
        max_gov_retries=flat.get("max_gov_retries"),
        hitl_mode=str(flat["hitl_mode"]) if flat.get("hitl_mode") is not None else None,
        hitl_once_per_turn=flat.get("hitl_once_per_turn"),
        policies=policies,
        active_profile=flat.get("active_profile"),
        error_recovery_plugin=(
            str(flat["error_recovery_plugin"]) if flat.get("error_recovery_plugin") is not None else None
        ),
        backtrack=flat.get("backtrack"),
        error_policy=flat.get("error_policy") if isinstance(flat.get("error_policy"), dict) else None,
        ingress_plugins=ingress_plugins,
    )


def _constructor_kwargs(plugin_cls: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    """Drop config a plugin cannot accept.

    ``priority`` orders attachments on a transition; it is not a constructor
    field. ``spec.governance.backtrack`` mixes plugin tuning (``repeat_threshold``)
    with session-level policy the controller enforces
    (``max_backtracks_per_session``, ``steps``, ``on_cap_reached``), so the
    whole dict cannot be splatted into every recovery plugin.
    """
    cfg = {key: value for key, value in cfg.items() if key != "priority"}
    try:
        params = inspect.signature(plugin_cls).parameters
    except (TypeError, ValueError):
        return dict(cfg)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(cfg)
    return {key: value for key, value in cfg.items() if key in params}


def build_kernel_config(
    binding: GovernanceBinding,
    *,
    pattern_plugin_id: str = "",
    agent_spec: dict[str, Any] | None = None,
    agent_id: str = "",
    manifest_dir: Any | None = None,
) -> Any:
    """Instantiate governance plugins and build a KernelConfig from a GovernanceBinding.

    The only function that turns a GovernanceBinding into instantiated
    governance plugins — mirrors mas.runtime.boundary.obs.plugins's role for
    observability. ctl/session/manifest_config.py::kernel_config_from_manifest
    (deprecated) delegates here rather than duplicating plugin resolution.
    """
    from mas.runtime.agent_defaults import default_pattern_plugin_id
    from mas.runtime.boundary.gov.filter import GovTransitionFilter
    from mas.runtime.boundary.gov.ingress_chain import RegisteredIngressPlugin
    from mas.runtime.boundary.gov.plugin import GovernancePlugin, GovernancePluginChain
    from mas.runtime.boundary.gov.policy_engine import GovernancePolicyEngine
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.registry import get_registry
    from mas.runtime.schema.governance import GovIngressProfile, GovPolicyProfile

    def _instantiate(name: str, cfg: dict[str, Any]) -> GovernancePlugin | None:
        """Resolve *name* to a registered governance plugin class and build it.

        No plugin is ever named here: every governance plugin goes through the same registry
        lookup by the name the spec gives. A name that is not registered as a governance
        plugin is an error.
        """
        variant = get_registry().resolve_by_type("governance", name)
        if variant is None:
            raise SpecBindingError(f"governance plugin {name!r} is not registered as a governance plugin")
        plugin_cls = variant.load_class()
        return plugin_cls(**_constructor_kwargs(plugin_cls, cfg))

    def _ingress_entry_for(plugin: GovernancePlugin, *, priority: int = 0) -> Any | None:
        # Ingress (tool-result) participation is opt-in and duck-typed via
        # .config.hitl_on_tool_result, not tied to any specific plugin class.
        plugin_cfg = getattr(plugin, "config", None)
        if plugin_cfg is not None and getattr(plugin_cfg, "hitl_on_tool_result", False):
            return RegisteredIngressPlugin(
                plugin=plugin,
                filter=GovTransitionFilter(hook="ingress", response_kind=("TOOL_RESULT",)),
                chain="stop",
                priority=priority,
            )
        return None

    def _ingress_sort_key(entry: RegisteredIngressPlugin) -> tuple[int, str]:
        name = getattr(entry.plugin, "plugin_id", None) or type(entry.plugin).__name__
        return (-entry.priority, str(name))

    egress_plugins: list[GovernancePlugin] = []
    ingress_entries: list[RegisteredIngressPlugin] = []

    for name in binding.plugins:
        plugin_cfg = dict(binding.plugin_configs.get(name) or {})
        priority = plugin_priority(plugin_cfg)
        plugin_cfg.pop("priority", None)
        plugin_cfg.pop("error_recovery_plugin", None)
        plugin_cfg.pop("backtrack", None)
        plugin_cfg.pop("error_policy", None)
        if manifest_dir is not None:
            plugin_cfg.setdefault("manifest_dir", str(manifest_dir))
        plugin = _instantiate(name, plugin_cfg)
        if plugin is not None:
            egress_plugins.append(plugin)
            entry = _ingress_entry_for(plugin, priority=priority)
            if entry is not None:
                ingress_entries.append(entry)

    if len(egress_plugins) == 1:
        egress_plugin: GovernancePlugin | None = egress_plugins[0]
    elif len(egress_plugins) > 1:
        egress_plugin = GovernancePluginChain(egress_plugins)
    else:
        egress_plugin = None

    kwargs: dict[str, Any] = {
        "pattern_plugin_id": pattern_plugin_id or default_pattern_plugin_id(),
        "agent_spec": agent_spec,
        "agent_id": agent_id or "",
    }
    if egress_plugin is not None:
        kwargs["egress_governance_plugin"] = egress_plugin
    if isinstance(binding.gov_policy_profile, str):
        kwargs["gov_policy_profile"] = GovPolicyProfile(binding.gov_policy_profile)
    for key, value in (
        ("gov_block_destructive", binding.gov_block_destructive),
        ("gov_trigger_destructive", binding.gov_trigger_destructive),
        ("hitl_on_tool", binding.hitl_on_tool),
        ("hitl_on_tool_result", binding.hitl_on_tool_result),
        ("hitl_once_per_turn", binding.hitl_once_per_turn),
        ("enable_memory_egress", binding.enable_memory_egress),
        ("enable_transport_egress", binding.enable_transport_egress),
        ("max_cot_pass", binding.max_cot_pass),
        ("max_gov_retries", binding.max_gov_retries),
    ):
        if value is not None:
            kwargs[key] = value
    if isinstance(binding.gov_ingress_profile, str):
        kwargs["gov_ingress_profile"] = GovIngressProfile(binding.gov_ingress_profile)

    if binding.backtrack is not None:
        if not isinstance(binding.backtrack, dict):
            raise SpecBindingError("spec.governance.backtrack must be an object")
        allowed = {"repeat_threshold", "max_backtracks_per_session", "steps", "on_cap_reached"}
        unknown = set(binding.backtrack) - allowed
        if unknown:
            raise SpecBindingError(f"spec.governance.backtrack: unknown field {sorted(unknown)[0]!r}")
        for key in ("repeat_threshold", "max_backtracks_per_session", "steps"):
            value = binding.backtrack.get(key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                raise SpecBindingError(f"spec.governance.backtrack.{key} must be an integer >= 1")
        if binding.backtrack.get("on_cap_reached", "hitl") not in {
            "hitl",
            "terminate",
            "stop_backtracking",
        }:
            raise SpecBindingError(
                "spec.governance.backtrack.on_cap_reached must be hitl, terminate, or stop_backtracking"
            )

    recovery_entry: RegisteredIngressPlugin | None = None
    if binding.error_recovery_plugin:
        recovery_cfg = dict(binding.backtrack or {})
        plugin_cfg = dict(binding.plugin_configs.get(binding.error_recovery_plugin) or {})
        recovery_cfg.update(plugin_cfg)
        if binding.error_policy:
            recovery_cfg.setdefault("error_policy", binding.error_policy)
        recovery_plugin = _instantiate(
            binding.error_recovery_plugin,
            recovery_cfg,
        )
        if recovery_plugin is None:
            raise SpecBindingError(
                f"spec.governance.error_recovery_plugin {binding.error_recovery_plugin!r} was not found"
            )
        recovery_entry = RegisteredIngressPlugin(
            plugin=recovery_plugin,
            filter=GovTransitionFilter(hook="ingress", response_kind=("ERROR",)),
            chain="stop",
            priority=plugin_priority(plugin_cfg),
        )

    if binding.policies:
        from mas.runtime.boundary.gov.policy_engine import PolicyParseError

        try:
            kwargs["policy_engine"] = GovernancePolicyEngine.from_yaml({"policies": binding.policies})
        except PolicyParseError as exc:
            raise SpecBindingError(f"spec.governance: {exc}") from exc

    # Extend the ingress chain with explicitly declared ingress-only plugins.
    # Same-transition order is priority (higher first), not recovery-first.
    chain = list(ingress_entries)
    if recovery_entry is not None:
        chain.append(recovery_entry)
    for entry in binding.ingress_plugins:
        if not isinstance(entry, dict):
            raise SpecBindingError("spec.governance.ingress_plugins entries must be objects")
        name = str(entry.get("type") or entry.get("name") or "").strip()
        if not name:
            raise SpecBindingError(f"spec.governance.ingress_plugins entry missing type/name: {entry!r}")
        chain_mode = str(entry.get("chain") or "stop")
        if chain_mode not in {"stop", "continue"}:
            raise SpecBindingError("spec.governance.ingress_plugins.chain must be stop or continue")
        plugin_cfg = {
            key: value
            for key, value in entry.items()
            if key not in {"type", "name", "chain", "response_kind", "priority"}
        }
        plugin = _instantiate(name, plugin_cfg)
        if plugin is None:
            raise SpecBindingError(f"ingress governance plugin {name!r} was not found")
        raw_kinds = entry.get("response_kind")
        if raw_kinds is None:
            kinds: tuple[str, ...] = ("TOOL_RESULT",)
        elif isinstance(raw_kinds, str) and raw_kinds.strip():
            kinds = (raw_kinds.strip(),)
        elif (
            isinstance(raw_kinds, (list, tuple))
            and raw_kinds
            and all(isinstance(k, str) and k.strip() for k in raw_kinds)
        ):
            kinds = tuple(str(k).strip() for k in raw_kinds)
        else:
            raise SpecBindingError(
                "spec.governance.ingress_plugins.response_kind must be a string or a list of strings"
            )
        chain.append(
            RegisteredIngressPlugin(
                plugin=plugin,
                filter=GovTransitionFilter(hook="ingress", response_kind=kinds),
                chain=chain_mode,
                priority=plugin_priority(entry),
            )
        )
    chain.sort(key=_ingress_sort_key)
    if chain:
        kwargs["ingress_governance_plugins"] = chain

    # parallel_tool_calls is not part of GovernanceBinding; caller sets via RuntimeEngine
    return KernelConfig(**kwargs)


__all__ = ["GovernanceBinding", "SpecBindingError", "build_kernel_config", "parse_gov_spec"]
