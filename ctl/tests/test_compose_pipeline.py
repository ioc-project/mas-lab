#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas-ctl compose pipeline."""

from mas.ctl.deployment.load import default_deployment
from mas.ctl.workspace.config import (
    UserConfig,
    _ref_list,
    collect_mas_infra_refs,
    merge_infra_interceptors,
    merge_infra_refs,
)


def test_collect_mas_infra_refs_is_empty_for_spec_separation():
    config = {"spec": {"infra_refs": ["team:llm-proxy"]}}
    assert collect_mas_infra_refs(config) == []


def test_merge_infra_refs_order():
    merged = merge_infra_refs(
        workspace_refs=["b", "c"],
        cli_refs=["c", "d"],
    )
    assert merged == ["b", "c", "d"]


def test_merge_infra_refs_user_default_when_no_workspace_infra():
    merged = merge_infra_refs(
        workspace_refs=[],
        user_refs=["standard:production"],
        cli_refs=[],
    )
    assert merged == ["standard:production"]


def test_merge_infra_interceptors_user_default_pipeline_when_workspace_silent():
    merged = merge_infra_interceptors(
        workspace_interceptors=[],
        user_refs=["standard:llm-cache"],
        cli_interceptors=[],
    )
    assert merged == ["standard:llm-cache"]


def test_ref_list_accepts_a_string_or_a_list():
    assert _ref_list(None) == []
    assert _ref_list("") == []
    assert _ref_list("  ") == []
    assert _ref_list("standard:llm-cache") == ["standard:llm-cache"]
    assert _ref_list(["standard:llm-cache", "", "standard:retry"]) == ["standard:llm-cache", "standard:retry"]


def test_user_config_reads_default_pipeline_as_string_or_list(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("default_pipeline: standard:llm-cache\n", encoding="utf-8")
    monkeypatch.setattr("mas.ctl.workspace.config._user_config_path", lambda: cfg)
    assert UserConfig.load().default_pipeline == ["standard:llm-cache"]
    cfg.write_text("default_pipeline: [standard:llm-cache, standard:retry]\n", encoding="utf-8")
    assert UserConfig.load().default_pipeline == ["standard:llm-cache", "standard:retry"]


def test_merge_infra_interceptors_workspace_middleware_wins_over_user_default():
    merged = merge_infra_interceptors(
        workspace_interceptors=["standard:chaos-lite"],
        user_refs=["standard:llm-cache"],
        cli_interceptors=[],
    )
    assert merged == ["standard:chaos-lite"]


def test_default_deployment_local_inproc():
    dep = default_deployment()
    assert dep["spec"]["strategy"] == "local-inproc"
    assert dep["spec"]["bus"]["kind"] == "inproc"
