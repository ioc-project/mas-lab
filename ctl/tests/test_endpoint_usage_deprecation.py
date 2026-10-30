#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``usage: connect | listen``; the old ``use | deploy | use-and-deploy`` still work and warn."""

from __future__ import annotations

from typing import Any

import pytest
from mas.ctl.overrides.bind import expand_connects, expand_listens
from mas.ctl.validate import validate_data
from mas.ctl.validate.deprecations import collect_endpoint_usage_deprecation
from mas.runtime.spec.endpoint_usage import deprecated_usage, usage_connects, usage_listens, usage_modes


def _application(usage: Any) -> dict[str, Any]:
    return {
        "apiVersion": "infra/v1",
        "kind": "Application",
        "metadata": {"name": "agents"},
        "spec": {"endpoints": {"qa": {"protocol": "a2a", "url": "http://127.0.0.1:9006", "usage": usage}}},
    }


def _tool_servers(usage: Any) -> dict[str, Any]:
    return {
        "apiVersion": "infra/v1",
        "kind": "ToolServerRegistry",
        "metadata": {"name": "tools"},
        "spec": {
            "tool_servers": [{"id": "local-mcp", "protocol": "mcp", "url": "http://127.0.0.1:9001/mcp", "usage": usage}]
        },
    }


@pytest.mark.parametrize(
    ("value", "modes"),
    [
        (None, {"connect"}),
        ("connect", {"connect"}),
        ("listen", {"listen"}),
        (["connect", "listen"], {"connect", "listen"}),
        ("use", {"connect"}),
        ("deploy", {"listen"}),
        ("use-and-deploy", {"connect", "listen"}),
    ],
)
def test_usage_modes(value: Any, modes: set[str]) -> None:
    assert usage_modes(value) == modes
    assert usage_connects(value) is ("connect" in modes)
    assert usage_listens(value) is ("listen" in modes)


def test_deprecated_usage_names_the_replacement() -> None:
    assert deprecated_usage("use") == "connect"
    assert deprecated_usage("deploy") == "listen"
    assert deprecated_usage("use-and-deploy") == "[connect, listen]"
    assert deprecated_usage("connect") is None
    assert deprecated_usage(["connect", "listen"]) is None


@pytest.mark.parametrize("build", [_application, _tool_servers])
@pytest.mark.parametrize("usage", ["connect", "listen", ["connect", "listen"]])
def test_new_values_validate_without_warning(build: Any, usage: Any) -> None:
    result = validate_data(build(usage), source="test", kind="infra")
    assert result.ok
    assert not [i for i in result.issues if "usage" in i.message]


@pytest.mark.parametrize("build", [_application, _tool_servers])
@pytest.mark.parametrize(
    ("usage", "replacement"),
    [("use", "connect"), ("deploy", "listen"), ("use-and-deploy", "[connect, listen]")],
)
def test_old_values_validate_and_warn(build: Any, usage: str, replacement: str) -> None:
    result = validate_data(build(usage), source="test", kind="infra")
    assert result.ok
    warnings = [i for i in result.issues if i.level == "warning" and "deprecated" in i.message]
    assert len(warnings) == 1
    assert f"usage: {usage} is deprecated" in warnings[0].message
    assert f"use usage: {replacement}" in warnings[0].message
    assert warnings[0].path.endswith(".usage")


@pytest.mark.parametrize("build", [_application, _tool_servers])
@pytest.mark.parametrize("usage", ["bind", ["connect", "connect"], [], ["deploy"]])
def test_unknown_values_fail_the_schema(build: Any, usage: Any) -> None:
    result = validate_data(build(usage), source="test", kind="infra", strict=True)
    assert not result.ok


def test_bundle_rows_are_found() -> None:
    bundle = {
        "apiVersion": "infra/v1",
        "kind": "InfraBundle",
        "spec": {"items": [_application("deploy"), _tool_servers("use")]},
    }
    paths = [path for path, _ in collect_endpoint_usage_deprecation(bundle)]
    assert paths == [
        "spec.items[0].spec.endpoints.qa.usage",
        "spec.items[1].spec.tool_servers[local-mcp].usage",
    ]


def test_resolved_rows_warn_as_compose_passes_them() -> None:
    rows = [("endpoints.qa", {"usage": "use-and-deploy"}), ("tool_servers[t]", {"usage": "connect"})]
    assert collect_endpoint_usage_deprecation(rows=rows) == [
        ("endpoints.qa.usage", "usage: use-and-deploy is deprecated, still supported; use usage: [connect, listen]")
    ]


def test_cli_shortcuts_write_new_values() -> None:
    assert "usage: connect" in expand_connects(["qa=a2a://127.0.0.1:9006/"])[0]
    assert "usage: connect" in expand_connects(["t=mcp://127.0.0.1:9001/mcp#echo"])[0]
    assert "usage: listen" in expand_listens(["qa=a2a://127.0.0.1:9006/"])[0]
