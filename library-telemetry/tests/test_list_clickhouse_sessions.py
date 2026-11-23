#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ListClickhouseSessionsStep (needs the bench framework, part of the dev extra), against a loopback
fake ClickHouse with the mas.lab.connections resolvers replaced."""

from __future__ import annotations

import pytest
from fake_clickhouse import FakeClickHouse, echo_credentials
from mas.library.telemetry.exceptions import ClickHouseQueryError
from mas.library.telemetry.steps import list_clickhouse_sessions as mod

pytestmark = pytest.mark.asyncio


class _FakeCtx:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run


async def _run(monkeypatch, port: int, *, dry_run: bool = False, **config):
    conn = {"host": "127.0.0.1", "port": port, "user": "u", "database": "db"}
    monkeypatch.setattr("mas.lab.connections.resolve_clickhouse_conn", lambda: conn, raising=False)
    monkeypatch.setattr("mas.lab.connections.resolve_clickhouse_password", lambda: "p-canary", raising=False)
    return await mod.ListClickhouseSessionsStep("list-sessions", config).execute(_FakeCtx(dry_run))


async def test_dataset_id_required(monkeypatch):
    with pytest.raises(ValueError, match="dataset_id"):
        await _run(monkeypatch, 9)


async def test_dry_run_returns_empty_without_network(monkeypatch):
    out = await _run(monkeypatch, 9, dry_run=True, dataset_id="ds-1")
    assert out.data == {"session_ids": [], "count": 0, "dataset_id": "ds-1"}
    assert out.metadata["dry_run"] is True


async def test_rows_are_parsed_and_an_unparseable_line_is_skipped(monkeypatch):
    body = b'{"session_id": "s1"}\nnot json\n{"session_id": "s2"}\n\n'
    with FakeClickHouse(lambda _r: (200, {}, body)) as server:
        out = await _run(monkeypatch, server.port, dataset_id="ds-1", limit=5)
    assert out.data == {"session_ids": ["s1", "s2"], "count": 2, "dataset_id": "ds-1"}
    assert "LIMIT 5" in out.metadata["query"]
    assert server.requests[0]["credentials"] == "u:p-canary"


async def test_an_http_error_names_the_code_not_the_body(monkeypatch):
    # kills: the server's answer (which may echo the credentials it received) copied into the failure
    with FakeClickHouse(echo_credentials(500)) as server:
        with pytest.raises(ClickHouseQueryError, match=r"HTTPError \(code 500\)") as raised:
            await _run(monkeypatch, server.port, dataset_id="ds-1")
    assert "p-canary" not in str(raised.value) and raised.value.__cause__ is None


async def test_a_redirect_is_not_followed_with_the_credentials(monkeypatch):
    # kills: urllib following a redirect to another host with the credentials
    with FakeClickHouse() as other, FakeClickHouse(lambda _r: (302, {"Location": other.url}, b"")) as server:
        with pytest.raises(ClickHouseQueryError, match="code 302"):
            await _run(monkeypatch, server.port, dataset_id="ds-1")
    assert len(server.requests) == 1 and other.requests == []
