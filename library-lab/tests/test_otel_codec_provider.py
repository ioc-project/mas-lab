#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""library-lab is the single provider of the ``otel_traces`` codecs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from mas.lab.benchmark.codecs import get_codec, list_codecs
from mas.library.lab.codecs.otel_codecs import ClickHouseOtelCodec, OtlpOtelCodec


@pytest.mark.parametrize(
    ("store_type", "expected"),
    [("otlp", OtlpOtelCodec), ("clickhouse", ClickHouseOtelCodec)],
)
def test_otel_traces_codec_resolves_to_library_lab(store_type: str, expected: type) -> None:
    codec = get_codec("otel_traces", store_type)
    assert codec is expected
    assert codec.__module__.startswith("mas.library.lab.")


def test_library_telemetry_registers_no_codec() -> None:
    assert ("otel_traces", "otlp") in list_codecs()
    spec = importlib.util.find_spec("mas.library.telemetry")
    assert spec is not None and spec.submodule_search_locations
    root = Path(next(iter(spec.submodule_search_locations)))
    assert not (root / "codecs.py").exists()
    offenders = [str(p) for p in root.rglob("*.py") if "register_codec" in p.read_text(encoding="utf-8")]
    assert offenders == []
    # The manifest sits at the library root of a source checkout; an installed wheel has none.
    manifest = root.parents[3] / "library.yaml"
    if manifest.is_file():
        assert "codec" not in manifest.read_text(encoding="utf-8").lower()
