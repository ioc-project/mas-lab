#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from mas.lab.benchmark.codecs import get_codec, list_codecs


def test_list_codecs_contains_manifest_entries() -> None:
    codecs = list_codecs()
    assert ("otel_traces", "otlp") in codecs
    assert ("otel_traces", "clickhouse") in codecs
    assert ("events", "filesystem") in codecs


def test_get_codec_loads_from_manifest() -> None:
    codec_cls = get_codec("events", "filesystem")
    assert codec_cls.__name__ == "FilesystemEventsCodec"


def test_get_codec_unknown_raises() -> None:
    with pytest.raises(ValueError, match="No codec registered"):
        get_codec("missing", "missing")
