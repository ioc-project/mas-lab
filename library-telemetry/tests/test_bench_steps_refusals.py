#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bench steps and ClickHouse/OTLP header inputs: a bad input is one closed error, never a traceback."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest

pytest.importorskip("mas.lab.benchmark.pipeline")

from mas.library.telemetry import bench, clickhouse_http  # noqa: E402
from mas.library.telemetry.conversion.session import check_otlp_headers  # noqa: E402
from mas.library.telemetry.exceptions import ClickHouseQueryError  # noqa: E402
from mas.runtime.secrets import SecretRefError  # noqa: E402


def _ctx(tmp_path):
    return SimpleNamespace(
        step_outputs={},
        output_dir=None,
        scope_context=None,
        get_step_output_dir=lambda name: tmp_path / name,
    )


def _run(step_cls, config, tmp_path):
    return asyncio.run(step_cls("s", config).execute(_ctx(tmp_path)))


def test_a_step_without_its_input_file_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError, match="events.jsonl not found"):
        _run(bench.EventsToOtelStep, {}, tmp_path)
    with pytest.raises(FileNotFoundError, match="spans file not found"):
        _run(bench.VerifyOtelStep, {}, tmp_path)
    with pytest.raises(ValueError, match="no spans file"):
        _run(bench.ExportOtelStep, {}, tmp_path)
    with pytest.raises(ValueError, match="'session_id'"):
        _run(bench.ClickhouseDumpStep, {}, tmp_path)


def test_verify_writes_its_report_and_compare_needs_two_files(tmp_path, monkeypatch):
    spans = tmp_path / "spans.jsonl"
    spans.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(bench, "run_verify_spans", lambda *a, **k: {"ok": True, "errors": [], "stats": {}})
    _run(bench.VerifyOtelStep, {"spans_path": str(spans)}, tmp_path)
    assert (tmp_path / "s" / "otel_verify_report.json").is_file()
    with pytest.raises(ValueError, match="needs 'reference' and 'candidate'"):
        _run(
            bench.CompareOtelSpansStep,
            {"reference": str(spans), "candidate_filename": "missing.jsonl", "run_dir": str(tmp_path)},
            tmp_path,
        )
    monkeypatch.setattr(bench, "run_compare_spans", lambda *a, **k: {"passed": False})
    with pytest.raises(RuntimeError, match="span parity FAILED"):
        _run(
            bench.CompareOtelSpansStep,
            {"reference": str(spans), "candidate": str(spans), "assert_equivalent": True},
            tmp_path,
        )


def test_a_failed_export_is_an_error(tmp_path, monkeypatch):
    spans = tmp_path / "spans.jsonl"
    spans.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(bench, "run_push_otlp", lambda *a, **k: {"status": "error", "detail": "down"})
    with pytest.raises(RuntimeError, match="OTLP export failed"):
        _run(bench.ExportOtelStep, {"spans_path": str(spans)}, tmp_path)


def test_clickhouse_refuses_a_host_with_userinfo_and_reports_a_refused_connection():
    with pytest.raises(ValueError, match="host must be"):
        clickhouse_http.clickhouse_url({"host": "user@evil", "port": 8123})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(ClickHouseQueryError, match="connection to"):
        clickhouse_http.query({"host": "127.0.0.1", "port": port}, "SELECT 1", timeout=2)


def test_otlp_headers_must_be_a_mapping_of_strings_or_refs():
    with pytest.raises(SecretRefError, match="expected a mapping"):
        check_otlp_headers(["x"])
    with pytest.raises(SecretRefError, match="got int"):
        check_otlp_headers({"X-Trace": 3})


def test_dump_reads_the_session_through_the_resolved_connection(tmp_path, monkeypatch):
    from mas.lab import connections
    from mas.library.telemetry.artifact import OtelSpanSet

    seen = {}

    class _Spans:
        span_count = 1
        session_id = "sess-1"

        def save(self, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
            return path

    def fetch(conn, session_id, **kwargs):
        seen.update(conn=conn, session=session_id, password=kwargs["password"])
        return _Spans()

    monkeypatch.setattr(connections, "resolve_clickhouse_conn", lambda: {"host": "127.0.0.1", "port": 1})
    monkeypatch.setattr(connections, "resolve_clickhouse_password", lambda: "pw")
    monkeypatch.setattr(OtelSpanSet, "fetch_from_clickhouse", staticmethod(fetch))
    _run(bench.ClickhouseDumpStep, {"session_id": "sess-1"}, tmp_path)
    assert seen == {"conn": {"host": "127.0.0.1", "port": 1}, "session": "sess-1", "password": "pw"}
