#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LLMProxy refs with api_base stay leaves; model name selects the connection."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.infra.resolve import resolve_infra_refs
from mas.runtime.registry.llm_provider_registry import leaf_for_model


def _proxy(path: Path, *, name: str, api_base: str, api_key_env: str, allowed: list[str]) -> Path:
    allowed_yaml = "\n".join(f"      - {item}" for item in allowed)
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        f"metadata: {{name: {name}}}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy:\n"
        f"    api_base: {api_base}\n"
        f"    api_key_env: {api_key_env}\n"
        "  models:\n"
        "    allowed:\n"
        f"{allowed_yaml}\n",
        encoding="utf-8",
    )
    return path


def test_two_llm_proxy_refs_are_leaves(tmp_path: Path) -> None:
    arm = _proxy(
        tmp_path / "arm.yaml",
        name="arm",
        api_base="https://arm.example/v1",
        api_key_env="ARM_KEY",
        allowed=["provider-a/model-a"],
    )
    judge = _proxy(
        tmp_path / "judge.yaml",
        name="judge",
        api_base="https://judge.example/v1",
        api_key_env="JUDGE_KEY",
        allowed=["judge-model"],
    )
    infra = resolve_infra_refs([str(arm), str(judge)], anchor=tmp_path)
    assert set(infra.llm_proxies) == {"arm", "judge"}
    assert infra.llm_proxy["api_base"] == "https://arm.example/v1"
    assert infra.llm_proxies["judge"]["api_key_env"] == "JUDGE_KEY"
    assert infra.llm_proxies["arm"]["api_key_env"] == "ARM_KEY"
    assert leaf_for_model("judge-model", infra.llm_proxies)["api_base"] == "https://judge.example/v1"
    assert leaf_for_model("provider-a/model-a", infra.llm_proxies)["api_key_env"] == "ARM_KEY"


def test_alias_mapping_selects_leaf(tmp_path: Path) -> None:
    arm = tmp_path / "arm.yaml"
    arm.write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: arm}\n"
        "spec:\n  protocol: openai\n"
        "  proxy: {api_base: https://arm.example/v1, api_key_env: ARM_KEY}\n"
        "  models:\n    mappings: {default: provider-a/model-a}\n"
        "    allowed: [provider-a/model-a]\n",
        encoding="utf-8",
    )
    judge = _proxy(
        tmp_path / "judge.yaml",
        name="judge",
        api_base="https://judge.example/v1",
        api_key_env="JUDGE_KEY",
        allowed=["judge-model"],
    )
    infra = resolve_infra_refs([str(arm), str(judge)], anchor=tmp_path)
    assert leaf_for_model("default", infra.llm_proxies)["api_key_env"] == "ARM_KEY"


def _middleware(path: Path, *, name: str, middleware: str, params: str = "") -> Path:
    params_block = f"  params:\n{params}\n" if params else ""
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraMiddleware\n"
        f"metadata: {{name: {name}}}\n"
        "spec:\n"
        f"  middleware: {middleware}\n"
        f"{params_block}",
        encoding="utf-8",
    )
    return path


def test_cache_is_common_other_steps_clone_per_leaf(tmp_path: Path) -> None:
    arm = _proxy(
        tmp_path / "arm.yaml",
        name="arm",
        api_base="https://arm.example/v1",
        api_key_env="ARM_KEY",
        allowed=["provider-a/model-a"],
    )
    judge = _proxy(
        tmp_path / "judge.yaml",
        name="judge",
        api_base="https://judge.example/v1",
        api_key_env="JUDGE_KEY",
        allowed=["judge-model"],
    )
    cache = _middleware(tmp_path / "cache.yaml", name="cache", middleware="llm_cache")
    sched = _middleware(
        tmp_path / "sched.yaml",
        name="sched",
        middleware="llm_scheduler",
        params="    max_in_flight: 2\n",
    )
    infra = resolve_infra_refs([str(arm), str(judge), str(cache), str(sched)], anchor=tmp_path)
    assert [step["middleware"] for step in infra.llm_proxy["pipeline"]] == ["llm_cache"]
    for leaf in infra.llm_proxies.values():
        assert [step["middleware"] for step in leaf.get("pipeline") or []] == ["llm_scheduler"]


def test_bundle_entry_pipeline_stays_on_that_leaf(tmp_path: Path) -> None:
    _proxy(
        tmp_path / "arm.yaml",
        name="arm",
        api_base="https://arm.example/v1",
        api_key_env="ARM_KEY",
        allowed=["provider-a/model-a"],
    )
    _proxy(
        tmp_path / "judge.yaml",
        name="judge",
        api_base="https://judge.example/v1",
        api_key_env="JUDGE_KEY",
        allowed=["judge-model"],
    )
    _middleware(tmp_path / "cache.yaml", name="cache", middleware="llm_cache")
    bundle = tmp_path / "bundle.yaml"
    bundle.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraBundle\n"
        "metadata: {name: split}\n"
        "spec:\n"
        "  entries:\n"
        "    - id: cache\n"
        "      type: infra_middleware\n"
        "      ref: cache.yaml\n"
        "    - id: arm\n"
        "      type: llm_provider\n"
        "      ref: arm.yaml\n"
        "      pipeline:\n"
        "        - middleware: fault_inject\n"
        "          params: {rate: 0.5}\n"
        "    - id: judge\n"
        "      type: llm_provider\n"
        "      ref: judge.yaml\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs([str(bundle)], anchor=tmp_path)
    assert [step["middleware"] for step in infra.llm_proxy["pipeline"]] == ["llm_cache"]
    assert [step["middleware"] for step in infra.llm_proxies["arm"].get("pipeline") or []] == ["fault_inject"]
    assert infra.llm_proxies["judge"].get("pipeline") in (None, [])


def test_cli_override_preserves_leaves(tmp_path: Path) -> None:
    arm = _proxy(
        tmp_path / "arm.yaml",
        name="arm",
        api_base="https://arm.example/v1",
        api_key_env="ARM_KEY",
        allowed=["provider-a/model-a"],
    )
    judge = _proxy(
        tmp_path / "judge.yaml",
        name="judge",
        api_base="https://judge.example/v1",
        api_key_env="JUDGE_KEY",
        allowed=["judge-model"],
    )
    sched = _middleware(
        tmp_path / "sched.yaml",
        name="sched",
        middleware="llm_scheduler",
        params="    max_in_flight: 2\n",
    )
    infra = resolve_infra_refs(
        [str(arm), str(judge), str(sched)],
        anchor=tmp_path,
        overrides=("infra:metadata.description=probe",),
    )
    assert set(infra.llm_proxies) == {"arm", "judge"}
    assert infra.llm_proxies["judge"]["api_base"] == "https://judge.example/v1"
    assert infra.llm_proxies["judge"]["api_key_env"] == "JUDGE_KEY"
    for leaf in infra.llm_proxies.values():
        assert [step["middleware"] for step in leaf.get("pipeline") or []] == ["llm_scheduler"]


def test_same_name_later_file_replaces(tmp_path: Path) -> None:
    first = _proxy(
        tmp_path / "first.yaml",
        name="arm",
        api_base="https://old.example/v1",
        api_key_env="OLD_KEY",
        allowed=["old-model"],
    )
    later = tmp_path / "later.yaml"
    later.write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: arm}\n"
        "spec:\n  protocol: openai\n"
        "  proxy: {api_base: https://new.example/v1, api_key_env: NEW_KEY}\n"
        "  models:\n    allowed: [new-model]\n"
        "  interceptors:\n    - middleware: llm_scheduler\n      params: {max_in_flight: 3}\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs([str(first), str(later)], anchor=tmp_path)
    assert set(infra.llm_proxies) == {"arm"}
    leaf = infra.llm_proxies["arm"]
    assert leaf["api_base"] == "https://new.example/v1"
    assert leaf["api_key_env"] == "NEW_KEY"
    assert leaf.get("allowed") == ["new-model"]
    assert [step["middleware"] for step in leaf.get("pipeline") or []] == ["llm_scheduler"]


def test_unclaimed_model_with_several_leaves_is_error() -> None:
    leaves = {
        "arm": {"api_base": "https://arm.example/v1", "api_key_env": "ARM_KEY", "allowed": ["m-a"]},
        "judge": {"api_base": "https://judge.example/v1", "api_key_env": "JUDGE_KEY", "allowed": ["m-b"]},
    }
    with pytest.raises(KeyError, match="not claimed"):
        leaf_for_model("unknown-model", leaves, default=leaves["arm"])


def test_two_star_leaves_error(tmp_path: Path) -> None:
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: a}\n"
        "spec:\n  proxy: {api_base: https://a.example/v1, api_key_env: A_KEY}\n",
        encoding="utf-8",
    )
    b.write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: b}\n"
        "spec:\n  proxy: {api_base: https://b.example/v1, api_key_env: B_KEY}\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs([str(a), str(b)], anchor=tmp_path)
    with pytest.raises(ValueError, match="empty models.allowed"):
        leaf_for_model("anything", infra.llm_proxies)
