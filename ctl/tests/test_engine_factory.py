#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine selection — live or llm_cache replay; no silent SimulatedEngine fallback."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.session.engine_factory import (
    _cache_read_enabled,
    _resolve_model_option,
    _resolve_sampling_param,
    _stream_enabled,
    _strict_replay,
    build_engine,
    resolve_model_name,
)
from mas.runtime.driver.mocks import AutoCtxAssembler

_CI_REPLAY = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm-cache" / "ci-replay.yaml"


def test_build_engine_errors_without_infra(monkeypatch, tmp_path):
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    ctx = AutoCtxAssembler()
    manifest = {"spec": {"models": [{"model": "gpt-4o"}]}}

    with pytest.raises(RuntimeError, match="No LLM configured"):
        build_engine(
            ctx,
            manifest,
            ResolvedInfra(refs=[], llm_proxy={}),
            anchor=tmp_path,
        )


def test_build_engine_live_requires_api_key(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ctx = AutoCtxAssembler()
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)

    with pytest.raises(RuntimeError, match="API key env unset: OPENAI_API_KEY"):
        build_engine(ctx, {"spec": {}}, infra, anchor=tmp_path)


def test_build_engine_cache_read_only_needs_no_api_key(monkeypatch, tmp_path):
    """Exclusive replay answers from the cache only, so it asks for no key."""
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.library.standard.plugins.llm.cache import CacheLLMProvider

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)

    sel = build_engine(
        AutoCtxAssembler(),
        {"spec": {"models": [{"model": "gpt-4o"}]}},
        infra,
        anchor=tmp_path,
        cache_read_only=True,
    )
    provider = sel.engine.llm_provider
    assert sel.mode == "replay"
    assert isinstance(provider, CacheLLMProvider)
    assert provider.raise_on_miss is True and provider.allow_write is False


def test_build_engine_live_with_cache_still_requires_api_key(monkeypatch, tmp_path):
    """A readable cache does not excuse a missing key: a miss would fail mid-run."""
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = tmp_path / "mas" / "llm_cache.json"
    cache.parent.mkdir(parents=True)
    cache.write_text('{"some-key": {"content": "Paris", "usage": {}, "source": "cache"}}', encoding="utf-8")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)

    with pytest.raises(RuntimeError, match="API key env unset: OPENAI_API_KEY"):
        build_engine(AutoCtxAssembler(), {"spec": {"models": [{"model": "gpt-4o"}]}}, infra, anchor=tmp_path)


def test_build_engine_rejects_a_placeholder_api_key(monkeypatch, tmp_path):
    """A pasted placeholder such as '…' used to fail deep inside the HTTP client."""
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "\u2026")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)

    with pytest.raises(RuntimeError, match="non-ASCII"):
        build_engine(AutoCtxAssembler(), {"spec": {"models": [{"model": "gpt-4o"}]}}, infra, anchor=tmp_path)


def test_parse_execution_rejects_removed_mocking_key():
    from mas.ctl.manifest.spec_bindings import SpecBindingError, parse_execution

    with pytest.raises(SpecBindingError, match="unknown field 'mocking'"):
        parse_execution({"mocking": {"enabled": True}})


def test_build_engine_replay_does_not_require_api_key(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
    from mas.runtime.engine.leaf import leaf_engine
    from mas.runtime.engine.llm_live import LiveLlmEngine

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ctx = AutoCtxAssembler()
    infra = resolve_infra_refs([str(_CI_REPLAY)], anchor=tmp_path)
    assert _strict_replay(infra.llm_proxy) is True

    sel = build_engine(
        ctx,
        {"spec": {"models": [{"id": "main", "model": "gpt-4o"}]}},
        infra,
        anchor=tmp_path,
    )
    assert sel.mode == "replay"
    assert isinstance(sel.engine, LiveLlmEngine)
    leaf = leaf_engine(sel.engine)
    assert leaf is sel.engine
    assert isinstance(leaf.llm_provider, OpenAILLMProvider)


def test_resolve_sampling_param_prefers_spec_models():
    manifest = {
        "spec": {
            "models": [{"model": "gpt-4", "temperature": 0.1, "max_tokens": 8000}],
            "llm": {"temperature": 0.9, "max_tokens": 500},
        }
    }
    assert _resolve_sampling_param(manifest, "temperature", 0.7) == 0.1
    assert _resolve_sampling_param(manifest, "max_tokens", 2000) == 8000


def test_resolve_sampling_param_matches_named_model_row():
    manifest = {
        "spec": {
            "models": [
                {"id": "main", "model": "gpt-4o", "temperature": 0.1},
                {"id": "alt", "model": "gpt-5-mini", "temperature": 0.9, "max_tokens": 111},
            ]
        }
    }
    assert _resolve_sampling_param(manifest, "temperature", 0.7, model="gpt-5-mini") == 0.9
    assert _resolve_sampling_param(manifest, "max_tokens", 2000, model="alt") == 111


def test_resolve_model_name_prefers_spec_models(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    manifest = {
        "spec": {
            "models": [{"model": "vertex_ai/gemini-2.5-pro"}],
            "llm": {"model": "gpt-4o"},
        }
    }
    assert resolve_model_name(manifest, None) == "vertex_ai/gemini-2.5-pro"


def test_resolve_model_name_any_uses_workspace_then_fails(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    manifest = {"spec": {"models": [{"model": "any"}]}}
    assert resolve_model_name(manifest, None, workspace_default="gpt-local") == "gpt-local"
    import pytest
    from mas.ctl.session.engine_factory import UnresolvedModelError

    with pytest.raises(UnresolvedModelError, match="will not substitute gpt-4o-mini"):
        resolve_model_name(manifest, None)


def test_build_engine_scripted_response_does_not_require_model(monkeypatch, tmp_path):
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.runtime.engine.simulated import SimulatedEngine

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    ctx = AutoCtxAssembler()
    manifest = {
        "spec": {
            "models": [],
            "design_pattern": {"type": "scripted_response", "params": {"default": {"text": "ok"}}},
        }
    }
    sel = build_engine(
        ctx,
        manifest,
        ResolvedInfra(refs=[], llm_proxy={}),
        pattern_plugin_id="react@v1",
        anchor=tmp_path,
    )
    assert sel.mode == "scripted"
    assert isinstance(sel.engine, SimulatedEngine)


def test_resolve_model_name_parent_mas_default(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    manifest = {"spec": {"models": [{"model": "any"}]}}
    assert resolve_model_name(manifest, None, parent_default="gpt-4o", workspace_default="gpt-local") == "gpt-4o"


def test_resolve_model_name_experiment_default(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    manifest = {"spec": {"models": [{"model": "any"}]}}
    assert (
        resolve_model_name(
            manifest,
            None,
            parent_default="any",
            experiment_default="gpt-4o",
            workspace_default="gpt-local",
        )
        == "gpt-4o"
    )


def test_resolve_model_name_cli_override_beats_spec(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    manifest = {"spec": {"models": [{"model": "gpt-4o"}]}}
    assert resolve_model_name(manifest, None, forced="gpt-4.1") == "gpt-4.1"


def test_resolve_model_name_unclaimed_with_several_leaves_is_error(monkeypatch):
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    infra = ResolvedInfra(
        refs=[],
        llm_proxy={"api_base": "https://arm.example/v1", "api_key_env": "ARM_KEY", "allowed": ["m-a"]},
        llm_proxies={
            "arm": {"api_base": "https://arm.example/v1", "api_key_env": "ARM_KEY", "allowed": ["m-a"]},
            "judge": {"api_base": "https://judge.example/v1", "api_key_env": "JUDGE_KEY", "allowed": ["m-b"]},
        },
    )
    with pytest.raises(KeyError, match="not claimed"):
        resolve_model_name({"spec": {"models": [{"model": "gpt-4o"}]}}, infra)


def test_resolve_sampling_param_falls_back_to_deprecated_spec_llm(caplog):
    manifest = {"spec": {"llm": {"temperature": 0.2, "max_tokens": 4096}}}
    with caplog.at_level(logging.WARNING):
        assert _resolve_sampling_param(manifest, "temperature", 0.7) == 0.2
        assert _resolve_sampling_param(manifest, "max_tokens", 2000) == 4096
    assert "spec.llm is deprecated" in caplog.text


def test_resolve_model_option_from_spec_models():
    manifest = {"spec": {"models": [{"model": "gpt-4", "reasoning_effort": "low"}]}}
    assert _resolve_model_option(manifest, "reasoning_effort") == "low"


def test_build_engine_loads_nested_reasoning_from_spec_models(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.runtime.engine.leaf import leaf_engine

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    ctx = AutoCtxAssembler()
    manifest = {
        "spec": {
            "models": [
                {
                    "model": "gpt-5",
                    "reasoning": {"effort": "low", "budget_tokens": 64, "exclude": True},
                }
            ]
        }
    }
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    sel = build_engine(ctx, manifest, infra, anchor=tmp_path)
    leaf = leaf_engine(sel.engine)
    assert leaf.reasoning.effort == "low"
    assert leaf.reasoning.budget_tokens == 64
    assert leaf.reasoning.exclude is True
    assert leaf.reasoning.exclude_from_spec is True


def test_build_engine_output_limits_from_infra_generation(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.runtime.engine.leaf import leaf_engine

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("MAS_LLM_MAX_TOKENS", raising=False)
    limits_ref = tmp_path / "limits.yaml"
    limits_ref.write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: limits}\n"
        "spec:\n  models:\n    generation:\n      max_tokens: 4096\n"
        "      max_output_tokens: 8000\n      on_truncation: escalate\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs(["standard:openai", str(limits_ref)], anchor=tmp_path)
    assert infra.llm_proxy["generation"]["max_output_tokens"] == 8000
    ctx = AutoCtxAssembler()
    manifest = {"spec": {"models": [{"model": "onprem/qwen3"}]}}

    leaf = leaf_engine(build_engine(ctx, manifest, infra, anchor=tmp_path).engine)
    assert leaf.output_limits.max_tokens == 4096
    assert leaf.output_limits.truncation.action == "escalate"

    capped = {"spec": {"models": [{"model": "onprem/qwen3", "max_tokens": 20000}]}}
    leaf = leaf_engine(build_engine(ctx, capped, infra, anchor=tmp_path).engine)
    assert leaf.output_limits.max_tokens == 8000

    bare = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    leaf = leaf_engine(build_engine(ctx, manifest, bare, anchor=tmp_path).engine)
    assert leaf.output_limits.budget is None


def test_cache_and_stream_from_runtime_engine():
    rt = {"cache": {"read": False}, "stream": True}
    assert _cache_read_enabled(rt) is False
    assert _stream_enabled(rt) is True


def test_build_engine_installs_infra_pipeline(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.runtime.engine.llm_live import LiveLlmEngine

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    infra.llm_proxy["pipeline"] = [
        {"middleware": "llm_cache", "params": {"allow_read": False, "allow_write": False}},
    ]
    sel = build_engine(
        AutoCtxAssembler(),
        {"spec": {"models": [{"model": "gpt-4o"}]}},
        infra,
        anchor=tmp_path,
    )
    assert type(sel.engine) is LiveLlmEngine
    assert sel.engine._infra_stack is not None


def test_build_engine_installs_tool_destinations(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.schema.egress import InvokeEngineIo

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    infra.tool_server_registry = {
        "local": {
            "id": "local",
            "protocol": "local",
            "tools": ["alpha"],
            "pipeline": [
                {
                    "middleware": "fault_inject",
                    "for": ["tool"],
                    "per": "destination",
                    "params": {"rate": 1.0, "message": "dest fault"},
                }
            ],
        }
    }
    sel = build_engine(
        AutoCtxAssembler(),
        {"spec": {"models": [{"model": "gpt-4o"}]}},
        infra,
        anchor=tmp_path,
    )
    assert type(sel.engine) is LiveLlmEngine
    assert sel.engine._tool_destinations
    sel.engine.set_scheduled_tool("alpha", {})
    hit = sel.engine.invoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL"))
    assert hit.response_kind == "ERROR"
    assert "dest fault" in (hit.text or "")
    sel.engine.set_scheduled_tool("beta", {})
    miss = sel.engine.invoke(InvokeEngineIo(correlation_id=2, op="TOOL_CALL"))
    assert "dest fault" not in (miss.text or "")


def test_build_engine_installs_tool_pipeline(monkeypatch, tmp_path):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.schema.egress import InvokeEngineIo

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    infra.tool_pipeline = [
        {
            "middleware": "fault_inject",
            "for": ["tool"],
            "per": "all",
            "params": {"rate": 1.0, "message": "injected fault"},
        }
    ]
    sel = build_engine(
        AutoCtxAssembler(),
        {"spec": {"models": [{"model": "gpt-4o"}]}},
        infra,
        anchor=tmp_path,
    )
    assert type(sel.engine) is LiveLlmEngine
    assert sel.engine._tool_stack is not None
    ret = sel.engine.invoke(InvokeEngineIo(correlation_id=1, op="TOOL_CALL"))
    assert ret.response_kind == "ERROR"
    assert "injected fault" in (ret.text or "")


def test_strict_replay_false_when_registry_empty(monkeypatch):
    class _Empty:
        def get_entry(self, urn: str):
            del urn
            return None

        def resolve_by_type(self, *args, **kwargs):
            del args, kwargs
            return None

    monkeypatch.setattr("mas.runtime.registry.get_registry", lambda: _Empty())
    assert (
        _strict_replay(
            {
                "pipeline": [
                    {"middleware": "llm_cache", "params": {"raise_on_miss": True}},
                ]
            }
        )
        is False
    )
