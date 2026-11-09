#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""spec/gov.py — governance plugin-list parsing and KernelConfig wiring."""

from __future__ import annotations

import pytest
from mas.runtime.spec.gov import SpecBindingError, build_kernel_config, parse_gov_spec


class _CustomGovernancePlugin:
    """Module-level (not nested in a test) so the registry's load_class() —
    importlib.import_module(self.module) then getattr — can actually find it."""

    def __init__(self, **cfg: object) -> None:
        self.cfg = cfg

    def evaluate_egress(self, intent, *, config):
        from mas.runtime.kernel.coupling import GovDecision

        return GovDecision.ALLOW, "custom", ""


class _CompositeAllowPlugin:
    def evaluate_egress(self, intent, *, config):
        from mas.runtime.kernel.coupling import GovDecision

        return GovDecision.ALLOW, "allow", "ok"


class _CompositeBlockPlugin:
    def evaluate_egress(self, intent, *, config):
        from mas.runtime.kernel.coupling import GovDecision

        return GovDecision.BLOCK, "block", "nope"


class _IngressOnlyPlugin:
    plugin_id = "test_ingress_only"

    def __init__(self, **config):
        self.config = config

    def evaluate_ingress(self, intent, *, config):
        from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision
        from mas.runtime.schema.governance import GovernanceAction

        return IngressGovDecision(action=GovernanceAction.BLOCK, message="ingress test")


class _ErrorIngressPlugin:
    plugin_id = "test_error_ingress"

    def evaluate_ingress(self, intent, *, config):
        from mas.runtime.boundary.gov.ingress_plugin import IngressGovDecision
        from mas.runtime.schema.governance import GovernanceAction

        return IngressGovDecision(action=GovernanceAction.ALLOW, message="other error")


def test_same_plugin_key_last_config_wins() -> None:
    binding = parse_gov_spec(
        [
            {
                "sample_governance": {
                    "policies": [{"name": "budget-cap", "trigger": {"on": "budget_threshold"}, "action": "block"}]
                }
            },
            {
                "sample_governance": {
                    "policies": [{"name": "forbidden-destination", "trigger": {"on": "tool_input"}, "action": "block"}]
                }
            },
        ]
    )
    names = {p["name"] for p in binding.policies}
    assert names == {"forbidden-destination"}
    assert binding.plugins == ["sample_governance"]


def test_same_plugin_key_scalar_field_last_value_wins() -> None:
    binding = parse_gov_spec(
        [
            {"sample_governance": {"hitl_on_tool": False}},
            {"sample_governance": {"hitl_on_tool": True}},
        ]
    )
    assert binding.hitl_on_tool is True


def test_build_kernel_config_wires_merged_policies() -> None:
    binding = parse_gov_spec(
        [
            {
                "sample_governance": {
                    "policies": [
                        {
                            "name": "p1",
                            "trigger": {
                                "on": "tool_input",
                                "tool": "*",
                                "condition": "",
                                "evaluation": "deterministic",
                            },
                            "action": "log",
                        }
                    ]
                }
            },
            {
                "sample_governance": {
                    "policies": [
                        {
                            "name": "p2",
                            "trigger": {
                                "on": "tool_input",
                                "tool": "*",
                                "condition": "",
                                "evaluation": "deterministic",
                            },
                            "action": "log",
                        }
                    ]
                }
            },
        ]
    )
    config = build_kernel_config(binding)
    assert config.policy_engine is not None
    assert {p.name for p in config.policy_engine.policies} == {"p2"}


def test_malformed_policy_raises_spec_binding_error_regression() -> None:
    """Regression: a malformed declarative policy (unrecognized action) used
    to raise a raw ValueError from deep inside PolicyDefinition.__post_init__,
    instead of this module's own SpecBindingError every other malformed
    governance shape raises."""
    binding = parse_gov_spec(
        [
            {
                "sample_governance": {
                    "policies": [
                        {"name": "bad-policy", "trigger": {"on": "tool_input"}, "action": "not_a_real_action"},
                    ]
                }
            }
        ]
    )
    with pytest.raises(SpecBindingError, match="bad-policy"):
        build_kernel_config(binding)


def test_malformed_policy_missing_trigger_key_raises_spec_binding_error() -> None:
    binding = parse_gov_spec(
        [
            {
                "sample_governance": {
                    "policies": [
                        {"name": "no-trigger", "action": "block"},
                    ]
                }
            }
        ]
    )
    with pytest.raises(SpecBindingError, match="no-trigger"):
        build_kernel_config(binding)


def test_unknown_plugin_name_is_an_error() -> None:
    """A name the spec gives must be registered as a governance plugin, or the build fails."""
    with pytest.raises(SpecBindingError, match="'totally_unknown_plugin_name' is not registered"):
        build_kernel_config(parse_gov_spec(["totally_unknown_plugin_name"]))


def test_named_plugin_resolves_via_registry_not_a_hardcoded_check() -> None:
    """Regression: build_kernel_config used to hardcode
    `if name in {"sample_governance", "sample_governance@v1"}`. A plugin
    registered under an arbitrary urn/shortcut — not sample_governance, not
    anything build_kernel_config's source mentions by name — must resolve
    and construct correctly purely through the plugin registry.

    register_plugin mutates get_registry()'s process-wide singleton with no
    built-in unregister — clean up the entry/alias this test adds so it
    doesn't leak into any other test that happens to run in this same
    process (e.g. one that enumerates all registered governance plugins).
    """
    from mas.runtime.registry import get_registry, register_plugin

    urn = "mas.gov.test_gov_spec_custom_plugin"
    shortcut = "test_gov_spec_custom_governance"
    try:
        register_plugin(
            urn,
            _CustomGovernancePlugin,
            shortcuts=[shortcut],
            attributes={"plugin_type": "governance"},
        )

        binding = parse_gov_spec([{shortcut: {"threshold": 3}}])
        config = build_kernel_config(binding)

        assert isinstance(config.egress_governance_plugin, _CustomGovernancePlugin)
        assert config.egress_governance_plugin.cfg == {"threshold": 3}
    finally:
        reg = get_registry()
        reg.unregister(urn)


def test_kernel_flags_without_a_named_plugin_build_no_plugin() -> None:
    """No plugin is named by the runtime: a flag with no plugin leaves the parametric default."""
    import dataclasses

    binding = dataclasses.replace(parse_gov_spec([]), hitl_on_tool=True)
    config = build_kernel_config(binding)
    assert config.egress_governance_plugin is None
    assert config.hitl_on_tool is True


def test_agent_spec_threads_through_to_kernel_config() -> None:
    spec = {"tools": ["lookup_schedule"], "models": [{"model": "gpt-4o"}]}
    binding = parse_gov_spec(None)
    config = build_kernel_config(binding, agent_spec=spec)
    assert config.agent_spec == spec


def test_agent_spec_defaults_to_none() -> None:
    config = build_kernel_config(parse_gov_spec(None))
    assert config.agent_spec is None


def test_distinct_plugins_are_chained_not_last_wins() -> None:
    from mas.runtime.boundary.gov.plugin import GovernancePluginChain
    from mas.runtime.registry import get_registry, register_plugin

    allow_urn = "mas.gov.test_composite_allow"
    block_urn = "mas.gov.test_composite_block"
    try:
        register_plugin(
            allow_urn,
            _CompositeAllowPlugin,
            shortcuts=["test_composite_allow"],
            attributes={"plugin_type": "governance"},
        )
        register_plugin(
            block_urn,
            _CompositeBlockPlugin,
            shortcuts=["test_composite_block"],
            attributes={"plugin_type": "governance"},
        )
        config = build_kernel_config(
            parse_gov_spec(
                [
                    {"test_composite_block": {"priority": 0}},
                    {"test_composite_allow": {"priority": 10}},
                ]
            )
        )
        plugin = config.egress_governance_plugin
        assert isinstance(plugin, GovernancePluginChain)
        assert [type(item).__name__ for item in plugin.plugins] == [
            "_CompositeAllowPlugin",
            "_CompositeBlockPlugin",
        ]
    finally:
        reg = get_registry()
        for urn, _shortcut in (
            (allow_urn, "test_composite_allow"),
            (block_urn, "test_composite_block"),
        ):
            reg.unregister(urn)


def test_error_recovery_plugin_resolves_from_manifest_governance_binding() -> None:
    from mas.library.standard.plugins.governance.backtrack_on_error import BacktrackOnErrorPlugin
    from mas.runtime.registry import get_registry, register_plugin

    urn = "mas.gov.test_backtrack_on_error"
    shortcut = "test_backtrack_on_error"
    try:
        register_plugin(
            urn,
            BacktrackOnErrorPlugin,
            shortcuts=[shortcut],
            attributes={"plugin_type": "governance"},
        )
        binding = parse_gov_spec(
            [
                {
                    "sample_governance": {
                        "error_recovery_plugin": shortcut,
                        "backtrack": {"repeat_threshold": 2},
                    }
                }
            ]
        )

        config = build_kernel_config(binding)

        plugin = next(
            entry.plugin
            for entry in config.ingress_governance_plugins
            if isinstance(entry.plugin, BacktrackOnErrorPlugin)
        )
        assert plugin.repeat_threshold == 2
    finally:
        registry = get_registry()
        registry.unregister(urn)


def test_error_recovery_plugin_ignores_session_level_backtrack_fields() -> None:
    """The shipped openclaw overlay mixes plugin tuning with session policy."""
    from mas.library.standard.plugins.governance.backtrack_on_error import BacktrackOnErrorPlugin
    from mas.runtime.registry import get_registry, register_plugin

    urn = "mas.gov.test_backtrack_full_config"
    shortcut = "test_backtrack_full_config"
    try:
        register_plugin(
            urn,
            BacktrackOnErrorPlugin,
            shortcuts=[shortcut],
            attributes={"plugin_type": "governance"},
        )
        binding = parse_gov_spec(
            [
                {
                    "sample_governance": {
                        "error_recovery_plugin": shortcut,
                        "backtrack": {
                            "repeat_threshold": 2,
                            "max_backtracks_per_session": 3,
                            "steps": 1,
                            "on_cap_reached": "hitl",
                        },
                    }
                }
            ]
        )

        config = build_kernel_config(binding)

        plugin = next(
            entry.plugin
            for entry in config.ingress_governance_plugins
            if isinstance(entry.plugin, BacktrackOnErrorPlugin)
        )
        assert plugin.repeat_threshold == 2
    finally:
        registry = get_registry()
        registry.unregister(urn)


def test_backtrack_plugin_retries_first_error_and_requests_rollback_on_repeat() -> None:
    from mas.library.standard.plugins.governance.backtrack_on_error import BacktrackOnErrorPlugin
    from mas.runtime.boundary.gov.error_recovery import ErrorRecoveryAction, IngressErrorContext
    from mas.runtime.schema.governance import GovIngressProfile

    plugin = BacktrackOnErrorPlugin(repeat_threshold=2)
    context = IngressErrorContext(
        response_kind="ERROR",
        error_text="connection refused",
        retry_count=0,
        max_retries=2,
        profile=GovIngressProfile.PERMISSIVE,
    )

    first = plugin.decide(context)
    second = plugin.decide(IngressErrorContext(**{**context.__dict__, "retry_count": 1}))

    assert first.action is ErrorRecoveryAction.RETRY
    assert second.action is ErrorRecoveryAction.BACKTRACK


def test_ingress_plugins_from_manifest_are_wired_to_kernel_config() -> None:
    from mas.runtime.registry import get_registry, register_plugin

    urn = "mas.gov.test_ingress_only"
    shortcut = "test_ingress_only"
    try:
        register_plugin(
            urn,
            _IngressOnlyPlugin,
            shortcuts=[shortcut],
            attributes={"plugin_type": "governance"},
        )
        binding = parse_gov_spec(
            [{"sample_governance": {"ingress_plugins": [{"type": shortcut, "chain": "continue"}]}}]
        )

        config = build_kernel_config(binding)

        assert len(config.ingress_governance_plugins) == 1
        entry = config.ingress_governance_plugins[0]
        assert isinstance(entry.plugin, _IngressOnlyPlugin)
        assert entry.chain == "continue"
        assert entry.filter.response_kind == ("TOOL_RESULT",)
    finally:
        registry = get_registry()
        registry.unregister(urn)
        typed = registry._by_type.get("governance")
        if typed:
            registry._by_type["governance"] = [item for item in typed if item.urn != urn]


def test_error_recovery_plugin_orders_by_priority_not_first() -> None:
    from mas.library.standard.plugins.governance.backtrack_on_error import BacktrackOnErrorPlugin
    from mas.runtime.registry import get_registry, register_plugin

    recovery_urn = "mas.gov.test_recovery_priority"
    other_urn = "mas.gov.test_error_ingress"
    try:
        register_plugin(
            recovery_urn,
            BacktrackOnErrorPlugin,
            shortcuts=["test_recovery_priority"],
            attributes={"plugin_type": "governance"},
        )
        register_plugin(
            other_urn,
            _ErrorIngressPlugin,
            shortcuts=["test_error_ingress"],
            attributes={"plugin_type": "governance"},
        )
        binding = parse_gov_spec(
            [
                {
                    "sample_governance": {
                        "error_recovery_plugin": "test_recovery_priority",
                        "ingress_plugins": [{"type": "test_error_ingress", "response_kind": "ERROR", "priority": 10}],
                    }
                }
            ]
        )
        config = build_kernel_config(binding)
        ids = [
            getattr(entry.plugin, "plugin_id", type(entry.plugin).__name__)
            for entry in config.ingress_governance_plugins
        ]
        assert ids[0] == "test_error_ingress"
        assert "backtrack_on_error@v1" in ids
    finally:
        registry = get_registry()
        for urn, _shortcut in (
            (recovery_urn, "test_recovery_priority"),
            (other_urn, "test_error_ingress"),
        ):
            registry.unregister(urn)
            typed = registry._by_type.get("governance")
            if typed:
                registry._by_type["governance"] = [item for item in typed if item.urn != urn]
