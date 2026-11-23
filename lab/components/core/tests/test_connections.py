#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lab connection helpers take a ctl-bound mapping. They do not read the host file."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.lab.connections import (
    bind_user_connections,
    reset_user_connections,
    resolve_clickhouse_conn,
    resolve_clickhouse_password,
    resolve_neo4j_conn,
)
from mas.runtime.secrets import SecretRefError
from secret_fakes import bound_secrets


def test_clickhouse_uses_bound_connections_not_a_host_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "mas").mkdir()
    (tmp_path / "mas" / "connections.yaml").write_text(
        "connections:\n  clickhouse:\n    host: from-file.example\n    password: {secret: clickhouse}\n",
        encoding="utf-8",
    )
    with bound_secrets({}):
        unbound = resolve_clickhouse_conn()
        assert (unbound["host"], unbound["uri"]) == ("localhost", "")  # the host file is not read
        assert resolve_clickhouse_password() == ""
    entry = {"host": "bound.example", "uri": "https://bound.example:8443", "password": {"secret": "clickhouse"}}
    token = _bound("clickhouse", entry)
    try:
        with bound_secrets({"clickhouse": "pw"}):
            conn = resolve_clickhouse_conn()
            assert (conn["host"], conn["uri"]) == ("bound.example", "https://bound.example:8443")
            assert resolve_clickhouse_password() == "pw"
    finally:
        reset_user_connections(token)


def _bound(name: str, entry: dict):
    return bind_user_connections({"connections": {name: entry}})


def test_nothing_declared_means_no_password_even_for_a_local_server() -> None:
    # kills: an implicit default secret sent to a server that declared none
    with bound_secrets({"clickhouse": "ch", "neo4j": "n4"}):
        assert resolve_clickhouse_password() == ""
        assert "password" not in resolve_clickhouse_conn() and "password" not in resolve_neo4j_conn(store_id="")


@pytest.mark.parametrize(
    ("password", "secrets", "expected"),
    [
        ({"secret": "ch-pass"}, {"ch-pass": "ch"}, "ch"),
        ({"secret": {"name": "ch-pass", "optional": True}}, {}, ""),
        ("", {"ch-pass": "ch"}, ""),
    ],
)
def test_a_declared_password(password: object, secrets: dict, expected: str) -> None:
    with bound_secrets(secrets):
        token = _bound("clickhouse", {"host": "ch.example", "password": password})
        try:
            assert resolve_clickhouse_password() == expected
        finally:
            reset_user_connections(token)


def test_a_literal_password_raises_without_its_value() -> None:
    with bound_secrets({}):
        token = _bound("clickhouse", {"password": "S3CR3T-literal"})
        try:
            with pytest.raises(SecretRefError) as caught:
                resolve_clickhouse_password()
            assert "S3CR3T-literal" not in str(caught.value)
        finally:
            reset_user_connections(token)
