#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A2A gRPC is extra a2a-grpc of library-ioa. Default import does not pull grpc."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from library_ioa.plugins.a2a.grpc_extra import (
    A2AGrpcExtraError,
    a2a_grpc_needed_message,
    a2a_row_grpc_reason,
    a2a_row_needs_grpc,
    collect_a2a_grpc_issues,
    load_a2a_grpc,
)
from library_ioa.plugins.a2a.server import start_grpc_server
from mas.ctl.validate import validate_data

_EXTRA = r"needs pip install 'mas-lab-library-ioa\[a2a-grpc\]'"


def test_importing_runtime_lab_and_default_a2a_does_not_import_grpc() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import mas.runtime; "
            "import mas.lab.cli; "
            "import library_ioa.plugins.a2a.agent_comm; "
            "import library_ioa.plugins.a2a.client; "
            "import library_ioa.plugins.a2a.exposure; "
            "mods = sorted(m for m in sys.modules if m == 'grpc' or m.startswith('grpc.')); "
            "print('grpc' if mods else 'no-grpc'); "
            "print(','.join(mods))",
        ],
        check=True,
        capture_output=True,
        env=dict(os.environ),
        text=True,
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "no-grpc", result.stdout + result.stderr


def test_row_needs_grpc_for_port_url_transport_and_protocol() -> None:
    from library_ioa.plugins.a2a.grpc_extra import a2a_plugin_type

    assert not a2a_row_needs_grpc({"protocol": "a2a", "url": "http://127.0.0.1:9005"})
    assert a2a_row_needs_grpc({"protocol": "a2a", "a2a": {"grpc_port": 9006}})
    assert a2a_row_needs_grpc({"protocol": "a2a", "a2a": {"grpc_url": "127.0.0.1:9006"}})
    assert a2a_row_needs_grpc({"protocol": "a2a", "transport": "GRPC"})
    assert a2a_row_needs_grpc({"protocol": "a2a+grpc"})
    assert not a2a_row_needs_grpc({"protocol": "mcp"})
    assert not a2a_row_needs_grpc({"protocol": "mcp", "transport": "GRPC"})
    assert not a2a_row_needs_grpc({"transport": "GRPC"})
    assert a2a_row_grpc_reason(None) is None
    assert a2a_row_grpc_reason("a2a") is None
    assert not a2a_row_needs_grpc([])
    assert a2a_plugin_type("a2a+grpc") == "a2a"
    assert a2a_plugin_type("a2a-grpc") == "a2a-grpc"
    assert a2a_plugin_type("mcp") == "mcp"


def test_a2a_plus_grpc_protocol_configures_a2a_exposure() -> None:
    from unittest.mock import MagicMock

    from library_ioa.plugins.a2a.exposure import A2AExposure

    exposure = A2AExposure(
        endpoint={
            "protocol": "a2a+grpc",
            "usage": "listen",
            "url": "http://127.0.0.1:9005",
            "a2a": {"grpc_port": 9006},
        },
        webserver=MagicMock(),
    )
    assert exposure.grpc_port == 9006


def test_transport_names_the_extra_when_grpc_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    for name in list(sys.modules):
        if name == "grpc" or name.startswith("grpc."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "grpc" or name.startswith("grpc."):
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(A2AGrpcExtraError, match=_EXTRA):
        load_a2a_grpc()
    with pytest.raises(A2AGrpcExtraError, match=_EXTRA):
        asyncio.run(start_grpc_server(object(), host="127.0.0.1", port=1))


def test_validate_names_the_extra_when_grpc_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import library_ioa.plugins.a2a.grpc_extra as extra

    monkeypatch.setattr(extra, "grpc_is_importable", lambda: False)
    data = {
        "apiVersion": "infra/v1",
        "kind": "Application",
        "metadata": {"name": "agents"},
        "spec": {
            "endpoints": {
                "oracle": {
                    "protocol": "a2a",
                    "url": "http://127.0.0.1:9005",
                    "usage": "listen",
                    "a2a": {"grpc_port": 9006},
                }
            }
        },
    }
    issues = collect_a2a_grpc_issues(data)
    assert issues == [
        ("spec.endpoints.oracle", a2a_grpc_needed_message("spec.endpoints.oracle: a2a.grpc_port")),
    ]
    result = validate_data(data, kind="infra", strict=True, resolve_refs=False)
    assert not result.ok
    assert any("mas-lab-library-ioa[a2a-grpc]" in issue.message for issue in result.issues)


@pytest.mark.parametrize(
    ("row", "setting"),
    [
        ({"protocol": "a2a", "a2a": {"grpc_port": 9006}}, "a2a.grpc_port"),
        ({"protocol": "a2a", "a2a": {"grpc_url": "h:1"}}, "a2a.grpc_url"),
        ({"protocol": "a2a", "transport": "GRPC"}, "transport: GRPC"),
        ({"protocol": "a2a+grpc"}, "protocol: a2a+grpc"),
    ],
)
def test_the_message_names_the_setting_that_asked_for_grpc(
    monkeypatch: pytest.MonkeyPatch, row: dict, setting: str
) -> None:
    import library_ioa.plugins.a2a.grpc_extra as extra

    monkeypatch.setattr(extra, "grpc_is_importable", lambda: False)
    [(path, message)] = collect_a2a_grpc_issues({"endpoints": {"x": row}})
    assert path == "endpoints.x"
    assert setting in message
    assert "protocol a2a+grpc needs" not in message or setting == "protocol: a2a+grpc"


def test_the_unadvertised_a2a_grpc_protocol_alias_is_not_the_a2a_plugin() -> None:
    assert not a2a_row_needs_grpc({"protocol": "a2a-grpc"})


def test_a_row_that_asks_for_grpc_passes_when_grpc_is_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    import library_ioa.plugins.a2a.grpc_extra as extra

    monkeypatch.setattr(extra, "grpc_is_importable", lambda: True)
    row = {"protocol": "a2a", "a2a": {"grpc_port": 9006}}
    assert collect_a2a_grpc_issues({"endpoints": {"x": row}}) == []


def test_grpcio_alone_is_not_the_a2a_grpc_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """deepeval's grpcio must not make validate pass without the A2A gRPC stack."""
    import builtins

    import library_ioa.plugins.a2a.grpc_extra as extra

    blocked = (
        "a2a.server.request_handlers.grpc_handler",
        "a2a.types.a2a_pb2_grpc",
    )
    for name in list(sys.modules):
        if name in blocked or any(name.startswith(prefix + ".") for prefix in blocked):
            monkeypatch.delitem(sys.modules, name, raising=False)
    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name in blocked or any(name.startswith(prefix + ".") for prefix in blocked):
            raise ImportError("no a2a grpc extra")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert extra.grpc_is_importable() is False
    with pytest.raises(A2AGrpcExtraError, match=_EXTRA):
        extra.load_a2a_grpc()
    row = {"protocol": "a2a", "a2a": {"grpc_port": 9006}}
    [(path, message)] = extra.collect_a2a_grpc_issues({"endpoints": {"x": row}})
    assert path == "endpoints.x"
    assert "mas-lab-library-ioa[a2a-grpc]" in message


def test_serve_background_refuses_grpc_port() -> None:
    from unittest.mock import MagicMock

    from library_ioa.plugins.a2a.exposure import A2AExposure

    exposure = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "listen",
            "url": "http://127.0.0.1:9005",
            "a2a": {"grpc_port": 9006},
        },
        webserver=MagicMock(),
    )
    with pytest.raises(RuntimeError, match="mas-ctl serve"):
        exposure.serve_background({"metadata": {"name": "oracle"}}, lambda *_a, **_k: None)


def test_validate_has_no_grpc_error_with_the_real_import() -> None:
    pytest.importorskip("grpc")
    pytest.importorskip("a2a.types.a2a_pb2_grpc")
    pytest.importorskip("a2a.server.request_handlers.grpc_handler")
    data = {
        "apiVersion": "infra/v1",
        "kind": "Application",
        "metadata": {"name": "agents"},
        "spec": {
            "endpoints": {
                "oracle": {
                    "protocol": "a2a",
                    "url": "http://127.0.0.1:9005",
                    "usage": "listen",
                    "a2a": {"grpc_port": 9006},
                }
            }
        },
    }
    result = validate_data(data, kind="infra", strict=True, resolve_refs=False)
    assert not [i for i in result.issues if "pip install" in i.message]


def test_require_a2a_grpc_rows_loads_once_for_the_first_grpc_row(monkeypatch: pytest.MonkeyPatch) -> None:
    import library_ioa.plugins.a2a.grpc_extra as extra

    called: list[str] = []
    monkeypatch.setattr(extra, "require_a2a_grpc", lambda reason: called.append(reason))
    extra.require_a2a_grpc_rows(
        [
            ("a", {"protocol": "mcp"}),
            ("b", {"protocol": "a2a", "a2a": {"grpc_port": 1}}),
            ("c", {"protocol": "a2a+grpc"}),
        ]
    )
    assert called == ["a2a.grpc_port"]


def test_require_a2a_grpc_uses_load_a2a_grpc(monkeypatch: pytest.MonkeyPatch) -> None:
    import library_ioa.plugins.a2a.grpc_extra as extra

    monkeypatch.setattr(extra, "load_a2a_grpc", lambda reason=None: "grpc")
    assert extra.require_a2a_grpc("a2a.grpc_port") is None


def test_collect_finds_endpoints_inside_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import library_ioa.plugins.a2a.grpc_extra as extra

    monkeypatch.setattr(extra, "grpc_is_importable", lambda: False)
    issues = extra.collect_a2a_grpc_issues(
        {"groups": [{"endpoints": {"x": {"protocol": "a2a", "a2a": {"grpc_port": 1}}}}]}
    )
    assert len(issues) == 1
    assert issues[0][0] == "groups[0].endpoints.x"
    assert "a2a.grpc_port" in issues[0][1]


def test_unknown_a2a_client_name_raises() -> None:
    from library_ioa.plugins.a2a import client as a2a_client

    with pytest.raises(AttributeError, match="no attribute 'Missing'"):
        a2a_client.__getattr__("Missing")


def test_https_url_requires_listen_settings() -> None:
    from unittest.mock import MagicMock

    from library_ioa.plugins.a2a.exposure import A2AExposure

    with pytest.raises(ValueError, match="a2a.listen"):
        A2AExposure(
            endpoint={"protocol": "a2a", "usage": "listen", "url": "https://agents.example.test"},
            webserver=MagicMock(),
        )


def test_serve_blocking_needs_a_webserver_that_can_share_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from library_ioa.plugins.a2a import server as a2a_server
    from library_ioa.plugins.a2a.exposure import A2AExposure

    class _Grpc:
        async def stop(self, grace: object = None) -> None:
            return None

    async def _start(*_a: object, **_k: object) -> _Grpc:
        return _Grpc()

    monkeypatch.setattr(a2a_server, "start_grpc_server", _start)
    exposure = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "listen",
            "url": "http://127.0.0.1:9005",
            "a2a": {"grpc_port": 9006},
        },
        webserver=object(),
    )
    with pytest.raises(RuntimeError, match="shared-loop"):
        exposure.serve_blocking({"metadata": {"name": "oracle"}}, lambda *_a, **_k: None)


def test_start_grpc_server_names_the_extra_when_handler_imports_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    import library_ioa.plugins.a2a.grpc_extra as extra

    monkeypatch.setattr(extra, "load_a2a_grpc", lambda reason=None: object())
    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "a2a.server.request_handlers.grpc_handler" or name.startswith(
            "a2a.server.request_handlers.grpc_handler."
        ):
            raise ImportError("no handler")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(A2AGrpcExtraError, match=_EXTRA):
        asyncio.run(start_grpc_server(object(), host="127.0.0.1", port=1))


def test_the_sdk_client_names_stay_module_attributes_and_resolve_lazily() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import library_ioa.plugins.a2a.client as c\n"
            "assert 'A2ACardResolver' not in vars(c)\n"
            "import a2a.client as sdk\n"
            "assert c.A2ACardResolver is sdk.A2ACardResolver\n"
            "assert c.ClientConfig is sdk.ClientConfig and c.create_client is sdk.create_client\n"
            "try:\n    c.Missing\nexcept AttributeError:\n    print('ok')",
        ],
        check=True,
        capture_output=True,
        env=dict(os.environ),
        text=True,
    )
    assert result.stdout.strip() == "ok", result.stderr
