#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Codec tests — skipped unless the bench framework (codec base) is installed."""

from __future__ import annotations

import importlib.util

import pytest


def _has_codec_base() -> bool:
    try:
        return importlib.util.find_spec("mas.lab.benchmark.codecs.base") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


requires_codec_base = pytest.mark.skipif(
    not _has_codec_base(), reason="bench extra not installed"
)


def test_core_does_not_require_codec_base():
    import sys

    import mas.library.telemetry  # noqa: F401

    assert "mas.library.telemetry.codecs" not in sys.modules or _has_codec_base()


@requires_codec_base
def test_codec_class_attrs():
    from mas.library.telemetry.codecs import ClickHouseSpansCodec, OtlpSpansCodec

    assert (OtlpSpansCodec.artifact_kind, OtlpSpansCodec.store_type) == (
        "otel_traces",
        "otlp",
    )
    assert (ClickHouseSpansCodec.artifact_kind, ClickHouseSpansCodec.store_type) == (
        "otel_traces",
        "clickhouse",
    )


# No test_codec_entry_points_discoverable here: these codecs aren't
# registered as plugins at all (see the module docstring in
# mas.library.telemetry.codecs) -- there's no live mechanism to assert
# discoverability against yet.
