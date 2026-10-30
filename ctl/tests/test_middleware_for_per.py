#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Middleware for/per: two leaves, tool vs llm skip, mixed keys."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.infra.resolve import InfraResolveError, resolve_infra_refs
from mas.runtime.engine.infra_pipeline import filter_pipeline_for_kind, middleware_id


def _proxy(path: Path, *, name: str, api_base: str) -> Path:
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        f"metadata: {{name: {name}}}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy:\n"
        f"    api_base: {api_base}\n"
        "    api_key_env: KEY\n"
        "  models:\n"
        "    allowed: [m]\n",
        encoding="utf-8",
    )
    return path


def _mw(path: Path, *, name: str, middleware: str, extra: str = "") -> Path:
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraMiddleware\n"
        f"metadata: {{name: {name}}}\n"
        "spec:\n"
        f"  middleware: {middleware}\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


def test_cache_per_all_is_shared_across_two_connections(tmp_path: Path) -> None:
    arm = _proxy(tmp_path / "arm.yaml", name="arm", api_base="https://arm.example/v1")
    judge = _proxy(tmp_path / "judge.yaml", name="judge", api_base="https://judge.example/v1")
    cache = _mw(tmp_path / "cache.yaml", name="cache", middleware="llm_cache")
    infra = resolve_infra_refs([str(arm), str(judge), str(cache)], anchor=tmp_path)
    assert [middleware_id(step) for step in infra.llm_proxy["pipeline"]] == ["llm_cache"]
    for leaf in infra.llm_proxies.values():
        assert not filter_pipeline_for_kind(leaf.get("pipeline") or [], "llm")


def test_limiter_per_destination_clones_onto_each_connection(tmp_path: Path) -> None:
    arm = _proxy(tmp_path / "arm.yaml", name="arm", api_base="https://arm.example/v1")
    judge = _proxy(tmp_path / "judge.yaml", name="judge", api_base="https://judge.example/v1")
    sched = _mw(
        tmp_path / "sched.yaml",
        name="sched",
        middleware="llm_scheduler",
        extra="  params:\n    max_in_flight: 1\n",
    )
    infra = resolve_infra_refs([str(arm), str(judge), str(sched)], anchor=tmp_path)
    arm_steps = list(infra.llm_proxies["arm"]["pipeline"])
    judge_steps = list(infra.llm_proxies["judge"]["pipeline"])
    assert [middleware_id(step) for step in arm_steps] == ["llm_scheduler"]
    assert [middleware_id(step) for step in judge_steps] == ["llm_scheduler"]
    assert arm_steps is not judge_steps
    assert infra.llm_proxy.get("pipeline") in (None, [])


def test_tool_step_is_skipped_for_llm_and_the_reverse(tmp_path: Path) -> None:
    arm = _proxy(tmp_path / "arm.yaml", name="arm", api_base="https://arm.example/v1")
    llm_step = _mw(
        tmp_path / "sched.yaml",
        name="sched",
        middleware="llm_scheduler",
        extra="  for: llm\n",
    )
    tool_step = _mw(
        tmp_path / "fault.yaml",
        name="fault",
        middleware="fault_inject",
        extra="  for: tool\n  per: all\n  params:\n    rate: 1.0\n",
    )
    infra = resolve_infra_refs(
        [str(arm), str(llm_step), str(tool_step)],
        anchor=tmp_path,
    )
    llm_common = filter_pipeline_for_kind(infra.llm_proxy.get("pipeline") or [], "llm")
    llm_leaf = filter_pipeline_for_kind(infra.llm_proxies["arm"].get("pipeline") or [], "llm")
    tool_steps = filter_pipeline_for_kind(list(infra.tool_pipeline or []), "tool")
    assert [middleware_id(step) for step in llm_common + llm_leaf] == ["llm_scheduler"]
    assert [middleware_id(step) for step in tool_steps] == ["fault_inject"]
    assert "fault_inject" not in [middleware_id(step) for step in llm_common + llm_leaf]
    assert "llm_scheduler" not in [middleware_id(step) for step in tool_steps]


def test_applies_to_alias_warns(tmp_path: Path) -> None:
    import warnings

    path = tmp_path / "cache.yaml"
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraMiddleware\n"
        "metadata: {name: cache}\n"
        "spec:\n"
        "  middleware: llm_cache\n"
        "  applies_to: [LLM_CALL]\n",
        encoding="utf-8",
    )
    from mas.runtime.engine.call_kinds import reset_alias_warnings

    reset_alias_warnings()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        infra = resolve_infra_refs([str(path)], anchor=tmp_path)
    assert any("applies_to" in str(item.message) for item in caught)
    steps = list(infra.llm_proxy.get("pipeline") or [])
    assert steps
    assert steps[0].get("for") == ["llm"]


def test_pipeline_and_interceptors_mix_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "arm.yaml"
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: LLMProxy\n"
        "metadata: {name: arm}\n"
        "spec:\n"
        "  protocol: openai\n"
        "  proxy: {api_base: https://arm.example/v1, api_key_env: KEY}\n"
        "  models: {allowed: [m]}\n"
        "  pipeline:\n"
        "    - middleware: llm_scheduler\n"
        "  interceptors:\n"
        "    - middleware: llm_cache\n",
        encoding="utf-8",
    )
    with pytest.raises(InfraResolveError, match="mix"):
        resolve_infra_refs([str(path)], anchor=tmp_path)


def test_cli_override_round_trip_cache_params(tmp_path: Path) -> None:
    arm = _proxy(tmp_path / "arm.yaml", name="arm", api_base="https://arm.example/v1")
    cache = _mw(tmp_path / "cache.yaml", name="cache", middleware="llm_cache")
    infra = resolve_infra_refs(
        [str(arm), str(cache)],
        anchor=tmp_path,
        overrides=("infra:spec.interceptors[middleware=llm_cache].params.allow_write=false",),
    )
    steps = list(infra.llm_proxy.get("pipeline") or [])
    assert steps
    assert steps[0]["params"].get("allow_write") is False


def test_undeclared_step_stays_llm_only(tmp_path: Path) -> None:
    arm = _proxy(tmp_path / "arm.yaml", name="arm", api_base="https://arm.example/v1")
    custom = tmp_path / "audit.yaml"
    custom.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraMiddleware\n"
        "metadata: {name: audit}\n"
        "spec:\n"
        "  middleware: my_custom_audit\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs([str(arm), str(custom)], anchor=tmp_path)
    leaf = list(infra.llm_proxies["arm"].get("pipeline") or [])
    assert [middleware_id(step) for step in leaf] == ["my_custom_audit"]
    assert leaf[0].get("for") == ["llm"]
    assert filter_pipeline_for_kind(leaf, "tool") == []


def test_destination_tool_step_clones_onto_each_server(tmp_path: Path) -> None:
    dest = _mw(
        tmp_path / "tap.yaml",
        name="tap",
        middleware="fault_inject",
        extra="  for: tool\n  per: destination\n  params:\n    rate: 1.0\n",
    )
    infra = resolve_infra_refs([str(dest)], anchor=tmp_path)
    servers = dict(infra.tool_server_registry or {})
    assert servers
    for server in servers.values():
        assert [middleware_id(step) for step in server.get("pipeline") or []] == ["fault_inject"]
    assert not filter_pipeline_for_kind(list(infra.tool_pipeline or []), "tool")


def test_wraps_attaches_middleware_to_named_leaf(tmp_path: Path) -> None:
    arm = _proxy(tmp_path / "arm.yaml", name="arm", api_base="https://arm.example/v1")
    judge = _proxy(tmp_path / "judge.yaml", name="judge", api_base="https://judge.example/v1")
    sched = _mw(
        tmp_path / "sched.yaml",
        name="sched",
        middleware="llm_scheduler",
        extra="  params:\n    max_in_flight: 1\n",
    )
    bundle = tmp_path / "bundle.yaml"
    bundle.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraBundle\n"
        "metadata: {name: wrapped}\n"
        "spec:\n"
        "  entries:\n"
        "    - id: sched\n"
        f"      ref: {sched.name}\n"
        "      wraps: [arm]\n"
        "    - id: arm\n"
        f"      ref: {arm.name}\n"
        "    - id: judge\n"
        f"      ref: {judge.name}\n",
        encoding="utf-8",
    )
    infra = resolve_infra_refs([str(bundle)], anchor=tmp_path)
    assert [middleware_id(step) for step in infra.llm_proxies["arm"].get("pipeline") or []] == [
        "llm_scheduler"
    ]
    assert not infra.llm_proxies["judge"].get("pipeline")


def test_mixed_for_and_applies_to_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "cache.yaml"
    path.write_text(
        "apiVersion: infra/v1\n"
        "kind: InfraMiddleware\n"
        "metadata: {name: cache}\n"
        "spec:\n"
        "  middleware: llm_cache\n"
        "  for: llm\n"
        "  applies_to: [LLM_CALL]\n",
        encoding="utf-8",
    )
    with pytest.raises(InfraResolveError, match="mix"):
        resolve_infra_refs([str(path)], anchor=tmp_path)
