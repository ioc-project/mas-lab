#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The one ClickHouse HTTP client: where credentials may go, and what a failure says."""

from __future__ import annotations

import urllib.parse

import pytest
from fake_clickhouse import FakeClickHouse, echo_credentials
from mas.library.telemetry import clickhouse_http
from mas.library.telemetry.exceptions import ClickHouseQueryError
from mas.runtime.secrets.transport import PlainHttpRefused

CANARY = "ch-canary-Hq52"


def _conn(port: int, host: str = "127.0.0.1", **extra: object) -> dict[str, object]:
    return {"host": host, "port": port, "user": "u", "database": "db", **extra}


def test_a_loopback_server_gets_the_credentials_over_plain_http() -> None:
    with FakeClickHouse(lambda _r: (200, {}, b"ok")) as server:
        assert clickhouse_http.query(_conn(server.port), "SELECT 1", password=CANARY) == "ok"
    assert server.requests[0]["credentials"] == f"u:{CANARY}" and "database=db" in server.requests[0]["path"]


@pytest.mark.parametrize("conn", [_conn(8123, "ch.example"), _conn(8443, "ch.example")])
def test_credentials_over_plain_http_to_a_remote_host_are_refused(conn: dict) -> None:
    with pytest.raises(PlainHttpRefused, match="http://ch.example") as raised:
        clickhouse_http.query(conn, "SELECT 1", password=CANARY)
    assert CANARY not in str(raised.value)


def test_a_failure_is_closed_text_without_the_server_answer() -> None:
    # kills: the server's answer (status reason, body: both echo the Basic credentials) in the error
    with FakeClickHouse(echo_credentials()) as server:
        with pytest.raises(ClickHouseQueryError) as raised:
            clickhouse_http.query(_conn(server.port), "SELECT 1", password=CANARY)
    assert CANARY not in str(raised.value) and "HTTPError (code 516)" in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("conn", "url"),
    [
        ({"host": "h", "port": 8443}, "http://h:8443"),
        ({"host": "::1", "port": 8123}, "http://[::1]:8123"),
        ({"uri": "https://ch.example/", "host": "ignored", "port": 1}, "https://ch.example"),
    ],
)
def test_clickhouse_url(conn: dict, url: str) -> None:
    assert clickhouse_http.clickhouse_url(conn) == url


def test_a_row_converts_to_an_sdk_span() -> None:
    row = {"StartNs": 1_700_000_000_000_000_000, "Duration": 2_000_000, "TraceId": "ABCD", "SpanId": "EF01"}
    row |= {"ParentSpanId": "0A0B", "SpanName": "step", "ServiceName": "svc", "StatusCode": "STATUS_CODE_ERROR"}
    span = clickhouse_http.row_to_sdk_span(row)
    assert (span["context"]["trace_id"], span["parent_id"]) == ("0xabcd", "0x0a0b")
    assert span["end_time"] > span["start_time"] and span["status"]["status_code"] == "ERROR"
    assert span["resource"]["attributes"]["service.name"] == "svc"
    assert clickhouse_http.row_to_sdk_span({**row, "ParentSpanId": ""})["parent_id"] is None


def test_a_trace_is_fetched_as_sdk_spans() -> None:
    from mas.library.telemetry import OtelSpanSet

    row = b'{"StartNs": 1, "TraceId": "AB", "SpanId": "CD", "SpanName": "step"}\n\n'
    with FakeClickHouse(lambda _r: (200, {}, row)) as server:
        spans = OtelSpanSet.fetch_from_clickhouse(_conn(server.port), "0xAB", by="trace", app_name="it's")
    sql = urllib.parse.parse_qs(urllib.parse.urlsplit(server.requests[0]["path"]).query)["query"][0]
    assert f"FROM db.otel_traces WHERE TraceId = '{'ab'.zfill(32)}' AND ServiceName = 'it\\'s'" in sql
    assert [s["context"]["span_id"] for s in spans.spans] == ["0xcd"]
