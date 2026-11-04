#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for ListClickhouseSessionsStep — skipped unless the bench framework
is installed (see tests/test_bench.py's requires_bench convention).

Mocks urllib.request.urlopen and mas.lab.connections.resolve_clickhouse_conn
directly (a lightweight HTTP GET, not the clickhouse_connect client), so no
real ClickHouse server or clickhouse-connect package is needed.
"""

from __future__ import annotations

import importlib.util
import urllib.error as _uerr

import pytest


def _has_bench() -> bool:
    try:
        return importlib.util.find_spec("mas.lab.benchmark.pipeline") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


requires_bench = pytest.mark.skipif(not _has_bench(), reason="bench extra not installed")
pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_conn(monkeypatch):
    from mas.library.telemetry.steps import list_clickhouse_sessions as mod

    conn = {"host": "ch.local", "port": 8123, "user": "u", "password": "p", "database": "db"}
    monkeypatch.setattr(
        "mas.lab.connections.resolve_clickhouse_conn", lambda: conn, raising=False
    )
    return mod


class _FakeCtx:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run


def _step(mod, **config):
    return mod.ListClickhouseSessionsStep("list-sessions", config)


@requires_bench
class TestListClickhouseSessionsStep:
    async def test_dataset_id_required(self):
        from mas.library.telemetry.steps import list_clickhouse_sessions as mod

        step = _step(mod)
        with pytest.raises(ValueError, match="dataset_id"):
            await step.execute(_FakeCtx())

    async def test_dry_run_returns_empty_without_network(self, monkeypatch):
        from mas.library.telemetry.steps import list_clickhouse_sessions as mod

        def _boom(*_a, **_kw):
            raise AssertionError("dry_run must not touch the network")

        monkeypatch.setattr(mod._req, "urlopen", _boom)
        step = _step(mod, dataset_id="ds-1")
        out = await step.execute(_FakeCtx(dry_run=True))
        assert out.data == {"session_ids": [], "count": 0, "dataset_id": "ds-1"}
        assert out.metadata["dry_run"] is True

    async def test_successful_query_parses_jsonl_rows(self, fake_conn, monkeypatch):
        mod = fake_conn
        raw = '{"session_id": "s1"}\n{"session_id": "s2"}\n\n'

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return raw.encode()

        monkeypatch.setattr(mod._req, "urlopen", lambda *a, **kw: _Resp())
        step = _step(mod, dataset_id="ds-1", limit=5)
        out = await step.execute(_FakeCtx())
        assert out.data == {
            "session_ids": ["s1", "s2"],
            "count": 2,
            "dataset_id": "ds-1",
        }
        assert "LIMIT 5" in out.metadata["query"]

    async def test_unparseable_line_is_skipped_not_fatal(self, fake_conn, monkeypatch):
        mod = fake_conn
        raw = '{"session_id": "s1"}\nnot json\n'

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return raw.encode()

        monkeypatch.setattr(mod._req, "urlopen", lambda *a, **kw: _Resp())
        step = _step(mod, dataset_id="ds-1")
        out = await step.execute(_FakeCtx())
        assert out.data["session_ids"] == ["s1"]

    async def test_missing_table_http_error_gets_friendly_message(self, fake_conn, monkeypatch):
        mod = fake_conn

        class _Body:
            def read(self):
                return b"Code: 60. UNKNOWN_TABLE"

            def close(self):
                pass

        def _raise(*_a, **_kw):
            raise _uerr.HTTPError("url", 404, "not found", {}, _Body())

        monkeypatch.setattr(mod._req, "urlopen", _raise)
        step = _step(mod, dataset_id="ds-1")
        with pytest.raises(RuntimeError, match="port-forwarded"):
            await step.execute(_FakeCtx())

    async def test_other_http_error_includes_code_and_body(self, fake_conn, monkeypatch):
        mod = fake_conn

        class _Body:
            def read(self):
                return b"internal error"

            def close(self):
                pass

        def _raise(*_a, **_kw):
            raise _uerr.HTTPError("url", 500, "server error", {}, _Body())

        monkeypatch.setattr(mod._req, "urlopen", _raise)
        step = _step(mod, dataset_id="ds-1")
        with pytest.raises(RuntimeError, match="ClickHouse error 500"):
            await step.execute(_FakeCtx())

    async def test_connection_failure_wrapped_as_runtime_error(self, fake_conn, monkeypatch):
        mod = fake_conn

        def _raise(*_a, **_kw):
            raise OSError("connection refused")

        monkeypatch.setattr(mod._req, "urlopen", _raise)
        step = _step(mod, dataset_id="ds-1")
        with pytest.raises(RuntimeError, match="ClickHouse connection failed"):
            await step.execute(_FakeCtx())
