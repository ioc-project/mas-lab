#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for mas-ctl compose pipeline."""

from mas.ctl.deployment.load import default_deployment
from mas.ctl.workspace.config import collect_mas_infra_refs, merge_infra_refs


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
        workspace_found=False,
    )
    assert merged == ["standard:production"]


def test_default_deployment_local_inproc():
    dep = default_deployment()
    assert dep["spec"]["strategy"] == "local-inproc"
    assert dep["spec"]["bus"]["kind"] == "inproc"
