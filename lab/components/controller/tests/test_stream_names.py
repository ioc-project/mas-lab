#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import json
import re
from pathlib import Path

from mas.lab.controller.ui_stream.streams import STREAM_TOPICS, expand_stream_names


def test_expand_stream_names_maps_aliases() -> None:
    assert expand_stream_names(["spec"]) == ["spec.snapshot", "spec.*"]
    assert expand_stream_names(["observability"]) == ["native.*"]
    assert expand_stream_names(["metrics"])[0:3] == ["metrics.snapshot", "metric.*", "native.metric"]
    assert "control.structure" in expand_stream_names(["control"])
    assert expand_stream_names(["native.*", "observability"]) == ["native.*"]


def test_expand_stream_names_keeps_raw_globs() -> None:
    assert expand_stream_names(["view.reset", "job.*"]) == ["view.reset", "job.*"]
    assert expand_stream_names(["*", "", None]) == []  # type: ignore[list-item]


def test_stream_topics_js_matches_python() -> None:
    js_path = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "demo-ui" / "src" / "streams.js"
        if candidate.is_file():
            js_path = candidate
            break
    assert js_path is not None, "demo-ui/src/streams.js not found"
    text = js_path.read_text(encoding="utf-8")
    match = re.search(r"export const STREAM_TOPICS = (\{.*?\});", text, re.S)
    assert match, f"STREAM_TOPICS missing in {js_path}"
    blob = re.sub(r"^(\s*)(\w+):", r'\1"\2":', match.group(1), flags=re.M)
    blob = re.sub(r",(\s*[}\]])", r"\1", blob)
    parsed = json.loads(blob)
    py = {k: list(v) for k, v in STREAM_TOPICS.items()}
    assert parsed == py
