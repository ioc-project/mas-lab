#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Application endpoint usage and infra resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    assert application_endpoint_usage(endpoint) == {"connect", "listen"}
    assert endpoint["a2a"]["listen"] == {"host": "0.0.0.0", "port": 9008}
    assert endpoint["a2a"]["grpc_port"] == 9007
    assert endpoint["a2a"]["grpc_url"] == "grpc://a2a.example.test:9007"
    assert endpoint["a2a"]["capabilities"]["streaming"] is False


@pytest.mark.parametrize(
    ("endpoint", "expected_usage", "used", "deployed"),
    [
        ({"usage": "connect"}, {"connect"}, True, False),
        ({"usage": "listen"}, {"listen"}, False, True),
        ({"usage": ["connect", "listen"]}, {"connect", "listen"}, True, True),
        ({"usage": ["listen"]}, {"listen"}, False, True),
        ({"usage": "use"}, {"connect"}, True, False),
        ({"usage": "deploy"}, {"listen"}, False, True),
        ({"usage": "use-and-deploy"}, {"connect", "listen"}, True, True),
        ({"expose": True}, {"connect", "listen"}, True, True),
        ({}, {"connect"}, True, False),
    ],
)
def test_application_endpoint_usage_modes(
    endpoint: dict[str, Any],
    expected_usage: set[str],
    used: bool,
    deployed: bool,
) -> None:
    assert application_endpoint_usage(endpoint) == expected_usage
    assert application_endpoint_is_used(endpoint) is used
    assert application_endpoint_is_deployed(endpoint) is deployed


@pytest.mark.parametrize("usage", ["bind", "", [], ["connect", "connect"], ["use"], 3])
def test_application_endpoint_usage_rejects_unknown(usage: Any) -> None:
    with pytest.raises(ValueError, match="usage must be 'connect', 'listen'"):
        application_endpoint_usage({"usage": usage})


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


def test_application_kind_has_a_schema_and_rejects_an_inline_secret(tmp_path: Path) -> None:
    def write(name: str, endpoint: dict) -> Path:
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "infra/v1",
                    "kind": "Application",
                    "metadata": {"name": name},
                    "spec": {"endpoints": {"buyer": endpoint}},
                }
            ),
            encoding="utf-8",
        )
        return path

    good = write("good", {"protocol": "a2a", "url": "http://127.0.0.1:9000", "usage": ["connect", "listen"]})
    as_application = validate_file(good, kind="application", strict=True)
    assert as_application.ok, as_application.issues
    assert as_application.kind == "application"
    as_infra = validate_file(good, kind="infra", strict=True)
    assert as_infra.ok, as_infra.issues

    bad = write("bad", {"protocol": "slim", "secret": "leaked-inline-secret-value-32bytesx"})
    failed = validate_file(bad, kind="application", strict=True)
    assert not failed.ok
    assert not validate_file(write("agent-only", {"usage": "connect"}), kind="application", strict=True).ok
    text = " ".join(issue.message for issue in failed.issues)
    assert "secret" in text or "additional" in text.lower()


def test_an_endpoint_row_may_carry_keys_the_schema_does_not_name(tmp_path: Path) -> None:
    """The schema names the keys it knows and refuses a secret; other keys were accepted before and still are."""
    path = tmp_path / "extra.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "infra/v1",
                "kind": "Application",
                "metadata": {"name": "extra"},
                "spec": {"endpoints": {"buyer": {"protocol": "a2a", "url": "http://127.0.0.1:9000", "weight": 3}}},
            }
        ),
        encoding="utf-8",
    )
    assert validate_file(path, kind="application", strict=True).ok
