#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The ``mas-lab telemetry`` ClickHouse commands: the password comes from the secret store, never argv, and a
failure is one line that does not repeat the server's answer."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner
from fake_clickhouse import FakeClickHouse, echo_credentials
from mas.lab.cli.commands.telemetry.apps import apps_group
from mas.lab.cli.commands.telemetry.dump import dump_cmd
from mas.lab.cli.commands.telemetry.sessions import sessions_group
from mas.lab.cli.commands.telemetry.traces import traces_group
from mas.lab.connections import bind_user_connections, reset_user_connections
from secret_fakes import bound_secrets

CANARY = "ch-canary-Lb27"


def _invoke(command: list[str], port: int, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-config")
    groups = {"apps": apps_group, "sessions": sessions_group, "traces": traces_group}
    if command[0] == "dump":
        cli, args = dump_cmd, [*command[1:], "-o", "/dev/null"]
    else:
        cli, args = groups[command[0]], command[1:]
    args += ["--host", "127.0.0.1", "--port", str(port), "--user", "default"]
    token = bind_user_connections({"clickhouse": {"password": {"secret": "clickhouse"}}})
    try:
        with bound_secrets({"clickhouse": CANARY}):
            return CliRunner().invoke(cli, args)
    finally:
        reset_user_connections(token)


COMMANDS = [["apps", "list"], ["sessions", "list"], ["traces", "list"], ["dump", "sess-1"]]


@pytest.mark.parametrize("command", COMMANDS)
def test_the_password_is_not_an_option(command: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: a plaintext ClickHouse password taken on argv
    result = _invoke([*command, "--password", "pw"], 9, monkeypatch)
    assert result.exit_code == 2 and "No such option: --password" in result.output


@pytest.mark.parametrize("command", COMMANDS)
def test_a_server_error_is_one_line_without_its_body(command: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: the server's answer (status reason, body: both echo the Basic credentials) printed by the CLI
    with FakeClickHouse(echo_credentials()) as server:
        result = _invoke(command, server.port, monkeypatch)
    assert server.requests[0]["credentials"] == f"default:{CANARY}"
    assert result.exit_code == 1
    assert CANARY not in result.output
    assert "HTTPError (code 516)" in result.output and "mas-ctl secrets set clickhouse" in result.output


def test_a_redirect_is_not_followed_with_the_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: urllib following a 302 to another host with the Authorization header
    with FakeClickHouse() as other:
        with FakeClickHouse(lambda _r: (302, {"Location": f"{other.url}/"}, b"")) as server:
            result = _invoke(["traces", "list"], server.port, monkeypatch)
    assert server.requests and other.requests == []
    assert result.exit_code == 1 and "code 302" in result.output


def test_a_loopback_server_is_not_reached_through_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # kills: HTTP_PROXY receiving the plain-http Basic credentials meant for a loopback ClickHouse
    with FakeClickHouse() as proxy, FakeClickHouse() as server:
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, proxy.url)
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        result = _invoke(["traces", "list"], server.port, monkeypatch)
    assert proxy.requests == [] and server.requests
    assert result.exit_code == 0


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("host", [f"u:{CANARY}@127.0.0.1", f"http://127.0.0.1/?password={CANARY}"])
def test_a_host_with_credentials_is_refused_without_echoing_it(
    command: list[str], host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-config")
    groups = {"apps": apps_group, "sessions": sessions_group, "traces": traces_group}
    cli, args = (
        (dump_cmd, [*command[1:], "-o", "/dev/null"]) if command[0] == "dump" else (groups[command[0]], command[1:])
    )
    with bound_secrets({"clickhouse": "pw"}):
        result = CliRunner().invoke(cli, [*args, "--host", host, "--port", "9"])
    assert result.exit_code == 2
    assert "--host" in result.output and "host name or address only" in result.output
    assert CANARY not in result.output


def test_a_configured_host_with_credentials_is_refused_before_connecting(capsys) -> None:
    from mas.lab.cli.commands.telemetry._clickhouse import ch_request

    with pytest.raises(SystemExit):
        ch_request({"host": f"u:{CANARY}@127.0.0.1", "port": 9, "user": ""}, "SELECT 1")
    err = capsys.readouterr().err
    assert CANARY not in err and "host name or address only" in err
