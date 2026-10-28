#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Application endpoint usage and infra resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.infra.resolve import (
    application_endpoint_is_deployed,
    application_endpoint_is_used,
    application_endpoint_usage,
    resolve_infra_refs,
)
from mas.ctl.overlay.merge import OverlayTargetError, merge_mas_overlay
from mas.ctl.validate import validate_file
from mas.ctl.workspace.config import WorkspaceConfig


def test_application_infra_preserves_protocol_listen_block(tmp_path: Path) -> None:
    infra_path = tmp_path / "agents.yaml"
    infra_path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "infra/v1",
                "kind": "Application",
                "metadata": {"name": "agents"},
                "spec": {
                    "endpoints": {
                        "weather-oracle": {
                            "protocol": "a2a",
                            "url": "http://127.0.0.1:9005",
                            "usage": "use-and-deploy",
                            "a2a": {
                                "listen": {"host": "0.0.0.0", "port": 9008},
                                "grpc_port": 9007,
                                "grpc_url": "grpc://a2a.example.test:9007",
                                "capabilities": {
                                    "streaming": False,
                                    "pushNotifications": False,
                                },
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    validation = validate_file(infra_path, kind="infra", strict=True)
    assert validation.ok, validation.issues

    resolved = resolve_infra_refs(
        [str(infra_path)],
        anchor=tmp_path,
        workspace=WorkspaceConfig({}),
    )

    assert resolved.applications["weather-oracle"]["url"] == "http://127.0.0.1:9005"
    assert resolved.applications["weather-oracle"]["protocol"] == "a2a"
    endpoint = resolved.applications["weather-oracle"]
    assert application_endpoint_usage(endpoint) == "use-and-deploy"
    assert endpoint["a2a"]["listen"] == {"host": "0.0.0.0", "port": 9008}
    assert endpoint["a2a"]["grpc_port"] == 9007
    assert endpoint["a2a"]["grpc_url"] == "grpc://a2a.example.test:9007"
    assert endpoint["a2a"]["capabilities"]["streaming"] is False


@pytest.mark.parametrize(
    ("endpoint", "expected_usage", "used", "deployed"),
    [
        ({"usage": "use"}, "use", True, False),
        ({"usage": "deploy"}, "deploy", False, True),
        ({"usage": "use-and-deploy"}, "use-and-deploy", True, True),
        ({"expose": True}, "use-and-deploy", True, True),
        ({}, "use", True, False),
    ],
)
def test_application_endpoint_usage_modes(
    endpoint: dict[str, str | bool],
    expected_usage: str,
    used: bool,
    deployed: bool,
) -> None:
    assert application_endpoint_usage(endpoint) == expected_usage
    assert application_endpoint_is_used(endpoint) is used
    assert application_endpoint_is_deployed(endpoint) is deployed


def test_mas_overlay_rejects_legacy_transport_fields() -> None:
    mas = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "spec": {
            "agency": {"agents": [{"id": "moderator", "ref": "moderator.yaml"}]},
            "workflow": {"entry": "moderator", "nodes": [{"id": "moderator"}]},
        },
    }
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {"agents": {"moderator": {"expose": {"kind": "a2a"}}}},
        },
    }

    with pytest.raises(OverlayTargetError, match="infra Application endpoint"):
        merge_mas_overlay(mas, overlay)
