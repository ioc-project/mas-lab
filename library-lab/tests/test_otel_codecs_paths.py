#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Connection resolution, the ClickHouse query and argument checks of the OTel codecs."""

from __future__ import annotations

import urllib.parse

import pytest
from fake_clickhouse import FakeClickHouse
from mas.lab.infra.datastore import DatastoreSpec
from mas.library.lab.codecs import otel_codecs as oc
from mas.library.telemetry.clickhouse_http import clickhouse_url
from secret_fakes import bound_secrets


def test_password_comes_from_the_secret_store() -> None:
    store = DatastoreSpec(
        host="ch.example", port=8123, password={"secret": "clickhouse"}, uri="http://ch.example:8123/"
    )
    with bound_secrets({"clickhouse": "secret"}):
        conn = oc._resolve_ch_conn(store)
    assert conn["password"] == "secret"
    assert clickhouse_url(conn) == "http://ch.example:8123"
    from mas.runtime.secrets.errors import SecretMissing

    assert oc._resolve_ch_conn(DatastoreSpec(host="ch.example", port=8123))["password"] == ""
    with pytest.raises(SecretMissing):  # a configured ref is required
        oc._resolve_ch_conn(DatastoreSpec(host="ch.example", port=8123, password={"secret": "clickhouse"}))


def test_decode_sends_the_store_password_and_filters_by_the_session_attribute() -> None:
    with FakeClickHouse() as server:
        with bound_secrets({"clickhouse": "CH-CANARY"}):
            store = DatastoreSpec(uri=server.url, user="u", database="db", password={"secret": "clickhouse"})
            assert oc.ClickHouseOtelCodec(store).decode(session_id="s1") == []
    assert server.requests[0]["credentials"] == "u:CH-CANARY"
    assert "mas.session.id" in urllib.parse.unquote_plus(server.requests[0]["path"])


def test_nothing_declared_means_no_password_even_for_a_loopback_server() -> None:
    # kills: an implicit default secret ``clickhouse`` sent to a server that declared none
    with bound_secrets({"clickhouse": "CH-CANARY"}):
        assert oc._resolve_ch_conn(DatastoreSpec(host="localhost", port=8123))["password"] == ""


def test_connection_data_does_not_reveal_the_connections_password() -> None:
    # kills: the codec failing because connections.yaml names a required password it never uses
    from mas.lab.connections import bind_user_connections, reset_user_connections

    token = bind_user_connections({"connections": {"clickhouse": {"password": {"secret": "absent"}}}})
    try:
        with bound_secrets({}):
            conn = oc._resolve_ch_conn(DatastoreSpec(host="ch.example", port=8123))
    finally:
        reset_user_connections(token)
    assert (conn["host"], conn["port"], conn["password"]) == ("ch.example", 8123, "")


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
