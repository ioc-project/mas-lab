#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Row conversion, connection resolution and argument checks of the OTel codecs."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest
from mas.lab.infra.datastore import DatastoreSpec
from mas.library.lab.codecs import otel_codecs as oc
from mas.runtime import hostenv


def test_clickhouse_row_converts_to_sdk_span() -> None:
    row = {
        "StartNs": 1_700_000_000_000_000_000,
        "Duration": 2_000_000,
        "TraceId": "ABCD",
        "SpanId": "EF01",
        "ParentSpanId": "0A0B",
        "SpanName": "step",
        "ServiceName": "svc",
        "StatusCode": "STATUS_CODE_ERROR",
        "SpanAttributes": {"k": "v"},
    }
    span = oc.ch_row_to_sdk_span(row)
    assert span["context"]["trace_id"] == "0xabcd"
    assert span["context"]["span_id"] == "0xef01"
    assert span["parent_id"] == "0x0a0b"
    assert span["start_time"].endswith("Z") and span["end_time"] > span["start_time"]
    assert span["status"]["status_code"] == "ERROR"
    assert span["resource"]["attributes"]["service.name"] == "svc"
    assert oc.ch_row_to_sdk_span({**row, "ParentSpanId": ""})["parent_id"] is None


def test_password_comes_from_the_host_environment() -> None:
    store = DatastoreSpec(host="ch.example", port=8123, password_env="CH_PW_PROBE", uri="http://ch.example:8123/")
    with hostenv.overlay({"CH_PW_PROBE": "secret"}):
        conn = oc._resolve_ch_conn(store)
    assert conn["password"] == "secret"
    assert oc._clickhouse_url(conn) == "http://ch.example:8123"
    assert oc._resolve_ch_conn(DatastoreSpec(host="ch.example", port=8123))["password"] != "secret"


def test_unreachable_clickhouse_says_where(monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="Could not connect to ClickHouse at http://ch.example:8123"):
        oc._clickhouse_query(DatastoreSpec(host="ch.example", port=8123), "select 1")


def test_clickhouse_decode_needs_an_id() -> None:
    codec = oc.ClickHouseOtelCodec(DatastoreSpec(host="ch.example", port=8123))
    with pytest.raises(ValueError, match="provide session_id, run_id, or trace_id"):
        codec.decode(by="trace", table="spans", app_name="app")


@pytest.mark.parametrize(
    "codec",
    [oc.OtlpOtelCodec, oc.FilesystemOtelCodec, oc.FilesystemEventsCodec],
)
def test_encode_refuses_an_unknown_artifact(codec, tmp_path) -> None:
    with pytest.raises(TypeError, match="expected list"):
        codec(DatastoreSpec(path=str(tmp_path))).encode(42)
