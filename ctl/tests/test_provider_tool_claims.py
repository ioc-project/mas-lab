#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tool-provider connection policy is infra-only."""

from __future__ import annotations

from pathlib import Path

from mas.ctl.validate.providers import check_provider_tool_claims
from mas.ctl.validate.validator import validate_data, validate_file


def test_agent_rejects_provider_connection_configuration():
    agent = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "test-agent"},
        "spec": {
            "description": "test",
            "providers": [
                {
                    "name": "mcp-tools",
                    "kind": "mcp",
                    "url": "http://127.0.0.1:9001/mcp",
                    "transport": "streamable-http",
                }
            ],
        },
    }
    result = validate_data(agent, kind="agent", resolve_refs=False)
    assert not result.ok


def test_agent_rejects_infra_registry_fields():
    agent = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "test-agent"},
        "spec": {
            "description": "test",
            "tool_servers": [{"id": "mcp-tools", "protocol": "mcp"}],
        },
    }
    result = validate_data(agent, kind="agent", resolve_refs=False)
    assert not result.ok


def test_mcp_tool_server_registry_is_valid_in_infra():
    repo = Path(__file__).resolve().parents[2]
    for sample in (
        repo / "library-samples/infra/mcp-localhost.yaml",
        repo / "library-samples/infra/mcp-localhost-deploy.yaml",
        repo / "docs/schemas/examples/infra/mcp-localhost.yaml",
        repo / "library-standard/src/mas/library/standard/libs/standard/local-tools.yaml",
    ):
        result = validate_file(sample, kind="infra")
        assert result.ok, (sample, result.issues)


def test_check_provider_tool_claims_rejects_unknown_explicit_name():
    agent = {"spec": {"tools": [{"name": "known"}], "providers": [{"tools": ["missing"]}]}}

    violations = check_provider_tool_claims(agent, "agent", None)

    assert len(violations) == 1
    assert "missing" in violations[0]


def test_check_provider_tool_claims_accepts_star_claim():
    agent = {"spec": {"tools": [], "providers": [{"tools": "*"}]}}

    assert check_provider_tool_claims(agent, "agent", None) == []
