#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for scoped resource management in the pipeline."""

import pytest
from mas.lab.benchmark.pipeline.resources import (
    _FACTORIES,
    ResourceRegistry,
    ResourceSpec,
    Scope,
    ScopeContext,
    resolve_resource_refs,
)


@pytest.fixture(autouse=True)
def _stub_kg_factory(monkeypatch):
    """Replace KG factory with a simple dict so tests don't need graph extension."""
    monkeypatch.setitem(_FACTORIES, "kg", lambda spec: {"_stub": True})


class TestScope:
    def test_ordering(self):
        assert Scope.EXPERIMENT < Scope.SCENARIO < Scope.TEST < Scope.RUN

    def test_from_str(self):
        assert Scope.from_str("experiment") == Scope.EXPERIMENT
        assert Scope.from_str("RUN") == Scope.RUN

    def test_from_str_invalid(self):
        with pytest.raises(KeyError):
            Scope.from_str("invalid")


class TestResourceSpec:
    def test_from_dict_defaults(self):
        spec = ResourceSpec.from_dict({"name": "kg1", "type": "kg"})
        assert spec.name == "kg1"
        assert spec.type == "kg"
        assert spec.scope == Scope.RUN
        assert spec.config == {}

    def test_from_dict_explicit_scope(self):
        spec = ResourceSpec.from_dict({"name": "shared", "type": "kg", "scope": "test", "config": {"x": 1}})
        assert spec.scope == Scope.TEST
        assert spec.config == {"x": 1}


class TestScopeContext:
    def test_at_scope_truncates(self):
        ctx = ScopeContext(experiment="e1", scenario="s1", test="t1", run="r1")
        trunc = ctx.at_scope(Scope.TEST)
        assert trunc.experiment == "e1"
        assert trunc.scenario == "s1"
        assert trunc.test == "t1"
        assert trunc.run == ""

    def test_scope_key_experiment(self):
        ctx = ScopeContext(experiment="e1", scenario="s1", test="t1", run="r1")
        assert ctx.scope_key(Scope.EXPERIMENT) == "e1"

    def test_scope_key_run(self):
        ctx = ScopeContext(experiment="e1", scenario="s1", test="t1", run="r1")
        assert ctx.scope_key(Scope.RUN) == "e1/s1/t1/r1"


class TestResourceRegistry:
    def _make_registry(self, *specs):
        return ResourceRegistry(list(specs))

    def test_get_creates_on_first_access(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.RUN)
        reg = self._make_registry(spec)
        ctx = ScopeContext(experiment="e", scenario="s", test="t", run="r1")
        obj = reg.get("kg1", ctx)
        assert obj is not None

    def test_same_scope_key_returns_same_instance(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.TEST)
        reg = self._make_registry(spec)
        ctx_r1 = ScopeContext(experiment="e", scenario="s", test="t", run="r1")
        ctx_r2 = ScopeContext(experiment="e", scenario="s", test="t", run="r2")
        # Test-scoped: both runs within same test should get same resource
        assert reg.get("kg1", ctx_r1) is reg.get("kg1", ctx_r2)

    def test_different_scope_key_returns_different_instance(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.TEST)
        reg = self._make_registry(spec)
        ctx_t1 = ScopeContext(experiment="e", scenario="s", test="t1", run="r1")
        ctx_t2 = ScopeContext(experiment="e", scenario="s", test="t2", run="r1")
        assert reg.get("kg1", ctx_t1) is not reg.get("kg1", ctx_t2)

    def test_run_scoped_fresh_per_run(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.RUN)
        reg = self._make_registry(spec)
        ctx_r1 = ScopeContext(experiment="e", scenario="s", test="t", run="r1")
        ctx_r2 = ScopeContext(experiment="e", scenario="s", test="t", run="r2")
        assert reg.get("kg1", ctx_r1) is not reg.get("kg1", ctx_r2)

    def test_get_unknown_raises(self):
        reg = self._make_registry()
        ctx = ScopeContext(experiment="e")
        with pytest.raises(KeyError, match="Unknown resource"):
            reg.get("nope", ctx)

    def test_reset_scope(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.RUN)
        reg = self._make_registry(spec)
        ctx = ScopeContext(experiment="e", scenario="s", test="t", run="r1")
        first = reg.get("kg1", ctx)
        reg.reset_scope(Scope.RUN, ctx)
        second = reg.get("kg1", ctx)
        assert first is not second

    def test_reset_narrower_than_preserves_wider(self):
        test_spec = ResourceSpec(name="tkg", type="kg", scope=Scope.TEST)
        run_spec = ResourceSpec(name="rkg", type="kg", scope=Scope.RUN)
        reg = self._make_registry(test_spec, run_spec)
        ctx = ScopeContext(experiment="e", scenario="s", test="t", run="r1")
        test_kg = reg.get("tkg", ctx)
        run_kg = reg.get("rkg", ctx)
        # Reset everything narrower than TEST → RUN-scoped should be new
        reg.reset_narrower_than(Scope.TEST, ctx)
        assert reg.get("tkg", ctx) is test_kg  # preserved
        assert reg.get("rkg", ctx) is not run_kg  # fresh

    def test_reset_all(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.EXPERIMENT)
        reg = self._make_registry(spec)
        ctx = ScopeContext(experiment="e")
        first = reg.get("kg1", ctx)
        reg.reset_all()
        second = reg.get("kg1", ctx)
        assert first is not second


class TestResolveResourceRefs:
    def test_string_ref_replaced(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.RUN)
        reg = ResourceRegistry([spec])
        ctx = ScopeContext(experiment="e", run="r1")
        result = resolve_resource_refs("@resource:kg1", reg, ctx)
        assert result is reg.get("kg1", ctx)

    def test_nested_dict(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.RUN)
        reg = ResourceRegistry([spec])
        ctx = ScopeContext(experiment="e", run="r1")
        config = {"step": {"kg": "@resource:kg1", "flag": True}}
        result = resolve_resource_refs(config, reg, ctx)
        assert result["step"]["kg"] is reg.get("kg1", ctx)
        assert result["step"]["flag"] is True

    def test_list_refs(self):
        spec = ResourceSpec(name="kg1", type="kg", scope=Scope.RUN)
        reg = ResourceRegistry([spec])
        ctx = ScopeContext(experiment="e", run="r1")
        config = ["@resource:kg1", "plain"]
        result = resolve_resource_refs(config, reg, ctx)
        assert result[0] is reg.get("kg1", ctx)
        assert result[1] == "plain"

    def test_no_refs_passthrough(self):
        reg = ResourceRegistry([])
        ctx = ScopeContext()
        config = {"a": 1, "b": "hello"}
        result = resolve_resource_refs(config, reg, ctx)
        assert result == config

    def test_non_string_passthrough(self):
        reg = ResourceRegistry([])
        ctx = ScopeContext()
        assert resolve_resource_refs(42, reg, ctx) == 42
        assert resolve_resource_refs(None, reg, ctx) is None
