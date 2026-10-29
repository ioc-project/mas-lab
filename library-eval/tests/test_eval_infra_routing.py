#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Eval routes the judge model name through LLMProxy leaves."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mas.library.eval.mce.runner import (
    _llm_call_with_retry,
    _pipeline_for_routed_model,
    _resolve_infra,
    _resolve_infra_generation,
    _resolve_infra_proxy,
    install_openai_llm_service,
)


def test_resolve_infra_routes_judge_model(monkeypatch) -> None:
    leaves = {
        "arm": {
            "api_base": "https://arm.example/v1",
            "api_key_env": "ARM_KEY",
            "allowed": ["provider-a/model-a"],
        },
        "judge": {
            "api_base": "https://judge.example/v1",
            "api_key_env": "JUDGE_KEY",
            "allowed": ["judge-model"],
            "default_model": "judge-model",
        },
    }
    proxy = {
        "api_base": "https://arm.example/v1",
        "api_key_env": "ARM_KEY",
        "default_model": "provider-a/model-a",
    }
    monkeypatch.setattr(
        "mas.library.eval.mce.runner._resolve_infra_proxy",
        lambda: (proxy, SimpleNamespace(), leaves),
    )
    monkeypatch.setattr("mas.runtime.agent_defaults.resolve_default_model", lambda _ws: None)
    api_base, api_key_env, _model = _resolve_infra(model="judge-model")
    assert api_base == "https://judge.example/v1"
    assert api_key_env == "JUDGE_KEY"


def test_judge_scheduler_steps_come_from_that_leaf() -> None:
    leaves = {
        "arm": {
            "api_base": "https://arm.example/v1",
            "api_key_env": "ARM_KEY",
            "allowed": ["provider-a/model-a"],
            "pipeline": [{"middleware": "llm_scheduler", "params": {"max_in_flight": 1}}],
        },
        "judge": {
            "api_base": "https://judge.example/v1",
            "api_key_env": "JUDGE_KEY",
            "allowed": ["judge-model"],
            "pipeline": [{"middleware": "llm_scheduler", "params": {"max_in_flight": 7}}],
        },
    }
    proxy = {"api_base": "https://arm.example/v1", "pipeline": []}
    steps = _pipeline_for_routed_model("judge-model", proxy, leaves)
    assert steps[0]["params"]["max_in_flight"] == 7
    arm_steps = _pipeline_for_routed_model("provider-a/model-a", proxy, leaves)
    assert arm_steps[0]["params"]["max_in_flight"] == 1


def test_resolve_infra_fallback_when_proxy_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "mas.library.eval.mce.runner._resolve_infra_proxy",
        lambda: (None, None, {}),
    )
    assert _resolve_infra() == ("", "OPENAI_API_KEY", "gpt-4o")
    assert _resolve_infra(model="judge-x") == ("", "OPENAI_API_KEY", "judge-x")


def test_resolve_infra_canonicalizes_judge_alias(monkeypatch) -> None:
    leaves = {
        "arm": {
            "api_base": "https://arm.example/v1",
            "api_key_env": "ARM_KEY",
            "allowed": ["provider-a/model-a"],
        },
        "judge": {
            "api_base": "https://judge.example/v1",
            "api_key_env": "JUDGE_KEY",
            "allowed": ["judge-model"],
            "mappings": {"judge-alias": "judge-model"},
            "default_model": "judge-model",
        },
    }
    proxy = {
        "api_base": "https://arm.example/v1",
        "api_key_env": "ARM_KEY",
        "default_model": "provider-a/model-a",
    }
    monkeypatch.setattr(
        "mas.library.eval.mce.runner._resolve_infra_proxy",
        lambda: (proxy, SimpleNamespace(), leaves),
    )
    monkeypatch.setattr("mas.runtime.agent_defaults.resolve_default_model", lambda _ws: None)
    api_base, api_key_env, resolved = _resolve_infra(model="judge-alias")
    assert api_base == "https://judge.example/v1"
    assert api_key_env == "JUDGE_KEY"
    assert resolved == "judge-model"


def test_resolve_infra_unclaimed_judge_is_error(monkeypatch) -> None:
    leaves = {
        "arm": {
            "api_base": "https://arm.example/v1",
            "api_key_env": "ARM_KEY",
            "allowed": ["provider-a/model-a"],
        },
        "judge": {
            "api_base": "https://judge.example/v1",
            "api_key_env": "JUDGE_KEY",
            "allowed": ["judge-model"],
        },
    }
    proxy = {
        "api_base": "https://arm.example/v1",
        "api_key_env": "ARM_KEY",
        "default_model": "provider-a/model-a",
    }
    monkeypatch.setattr(
        "mas.library.eval.mce.runner._resolve_infra_proxy",
        lambda: (proxy, SimpleNamespace(), leaves),
    )
    with pytest.raises(KeyError, match="not claimed"):
        _resolve_infra(model="unknown-judge")
    with pytest.raises(KeyError, match="not claimed"):
        _pipeline_for_routed_model("unknown-judge", proxy, leaves)


def test_resolve_infra_uses_leaf_default_when_model_omitted(monkeypatch) -> None:
    proxy = {
        "api_base": "https://arm.example/v1",
        "api_key_env": "ARM_KEY",
        "default_model": "provider-a/model-a",
    }
    monkeypatch.setattr(
        "mas.library.eval.mce.runner._resolve_infra_proxy",
        lambda: (proxy, SimpleNamespace(), {}),
    )
    monkeypatch.setattr("mas.runtime.agent_defaults.resolve_default_model", lambda _ws: None)
    api_base, api_key_env, resolved = _resolve_infra()
    assert api_base == "https://arm.example/v1"
    assert api_key_env == "ARM_KEY"
    assert resolved == "provider-a/model-a"


def test_resolve_infra_generation_from_proxy(monkeypatch) -> None:
    proxy = {"generation": {"max_tokens": 32}}
    monkeypatch.setattr(
        "mas.library.eval.mce.runner._resolve_infra_proxy",
        lambda: (proxy, SimpleNamespace(), {}),
    )
    assert _resolve_infra_generation() == {"max_tokens": 32}


def test_resolve_infra_proxy_import_error(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"mas.ctl.infra.resolve", "mas.lab.workspace"}:
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert _resolve_infra_proxy() == (None, None, {})


def test_resolve_infra_proxy_missing_workspace(monkeypatch) -> None:
    class _WS:
        found = False

        @classmethod
        def load(cls):
            return cls()

    monkeypatch.setattr("mas.lab.workspace.WorkspaceConfig", _WS)
    proxy, ws, leaves = _resolve_infra_proxy()
    assert proxy is None
    assert ws.found is False
    assert leaves == {}


def test_resolve_infra_proxy_no_refs(monkeypatch) -> None:
    class _WS:
        found = True
        effective_infra_refs: list = []

        @classmethod
        def load(cls):
            return cls()

    monkeypatch.setattr("mas.lab.workspace.WorkspaceConfig", _WS)
    proxy, ws, leaves = _resolve_infra_proxy()
    assert proxy is None
    assert ws.found is True
    assert leaves == {}


def test_resolve_infra_proxy_resolve_failure(monkeypatch) -> None:
    class _WS:
        found = True
        effective_infra_refs = ["infra:proxy"]

        @classmethod
        def load(cls):
            return cls()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("no infra")

    monkeypatch.setattr("mas.lab.workspace.WorkspaceConfig", _WS)
    monkeypatch.setattr("mas.ctl.infra.resolve.resolve_infra_refs", _boom)
    proxy, ws, leaves = _resolve_infra_proxy()
    assert proxy is None
    assert ws.found is True
    assert leaves == {}


def test_resolve_infra_proxy_returns_leaves(monkeypatch) -> None:
    class _WS:
        found = True
        effective_infra_refs = ["infra:proxy"]

        @classmethod
        def load(cls):
            return cls()

    resolved = SimpleNamespace(
        llm_proxy={"api_base": "https://arm.example/v1"},
        llm_proxies={"arm": {"api_base": "https://arm.example/v1"}},
    )
    monkeypatch.setattr("mas.lab.workspace.WorkspaceConfig", _WS)
    monkeypatch.setattr("mas.ctl.infra.resolve.resolve_infra_refs", lambda *_a, **_k: resolved)
    proxy, _ws, leaves = _resolve_infra_proxy()
    assert proxy == {"api_base": "https://arm.example/v1"}
    assert leaves == {"arm": {"api_base": "https://arm.example/v1"}}


def test_install_openai_llm_service_routes_and_is_idempotent(monkeypatch) -> None:
    import mas.library.eval.mce.runner as runner

    runner._jury_patched = False
    runner._effective_model = ""
    runner._openai_client = None
    runner._openai_base_url = ""
    runner._openai_api_key = ""

    proxy = {
        "api_base": "https://judge.example/v1",
        "pipeline": [{"middleware": "llm_scheduler", "params": {"max_in_flight": 7}}],
    }
    leaves = {"judge": dict(proxy, allowed=["judge-model"])}
    installed: list = []

    monkeypatch.setattr(
        runner,
        "_resolve_infra",
        lambda model=None: ("https://judge.example/v1", "JUDGE_KEY", model or "judge-model"),
    )
    monkeypatch.setattr(runner, "_resolve_infra_proxy", lambda: (proxy, SimpleNamespace(), leaves))
    monkeypatch.setattr(runner, "_resolve_infra_generation", lambda: {})
    monkeypatch.setattr(runner, "_make_deepeval_model", lambda **_k: None)
    monkeypatch.setenv("JUDGE_KEY", "test-key")

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("openai.OpenAI", _Client)
    monkeypatch.setattr(
        "mas.runtime.engine.llm_scheduler.install_policy_from_pipeline",
        lambda steps: installed.append(list(steps)),
    )

    class _Jury:
        def __init__(self, _cfg):
            pass

    class _LLMClient:
        query = None

    monkeypatch.setattr("metrics_computation_engine.llm_judge.jury.Jury", _Jury)
    monkeypatch.setattr("metrics_computation_engine.llm_judge.llm.LLMClient", _LLMClient)

    first = install_openai_llm_service(model_override="judge-model")
    assert first == "judge-model"
    assert installed
    assert installed[0][0]["params"]["max_in_flight"] == 7

    second = install_openai_llm_service(model_override="judge-model")
    assert second == "judge-model"
    assert len(installed) == 1


def test_llm_call_with_retry_reads_choice_content() -> None:
    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**_kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    assert _llm_call_with_retry(_Client(), "judge-model", [{"role": "user", "content": "hi"}]) == "ok"
