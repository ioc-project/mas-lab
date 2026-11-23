#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ClickHouse and collector targets fall back to the host environment."""

from __future__ import annotations

import pytest
from mas.library.telemetry.infra import ClickHouseTarget, clickhouse_target, resolve_otel_collector
from mas.runtime import hostenv


def test_empty_clickhouse_fields_fall_back_to_the_host_environment() -> None:
    target = ClickHouseTarget(host="", port=0, database="", user="")
    env = {
        "CLICKHOUSE_HOST": "ch.example",
        "CLICKHOUSE_PORT": "9000",
        "CLICKHOUSE_DATABASE": "traces",
        "CLICKHOUSE_USER": "reader",
    }
    with hostenv.overlay(env):
        assert (
            target.resolved_host(),
            target.resolved_port(),
            target.resolved_database(),
            target.resolved_user(),
        ) == ("ch.example", 9000, "traces", "reader")


def test_a_manifest_without_a_clickhouse_target_names_what_is_missing() -> None:
    with pytest.raises(ValueError, match=r"no 'ClickHouse' target"):
        clickhouse_target({"targets": []}, name="wanted")


def test_an_infra_without_an_endpoint_is_refused() -> None:
    with hostenv.overlay({"OTEL_EXPORTER_OTLP_ENDPOINT": ""}), pytest.raises(ValueError, match="no OTLP endpoint"):
        resolve_otel_collector({"targets": [{"kind": "OtelCollector", "name": "c", "endpoint": ""}]})
