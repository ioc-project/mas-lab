#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from mas.library.lab.steps.extract.trace_stats import empty_trace_stats, fold_trace_event, public_trace_stats

_FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_parity" / "single_run" / "events.jsonl"


def _load() -> list[dict]:
    return [json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_pipeline_parity_corpus_trace_stats() -> None:
    events = _load()
    cold = empty_trace_stats()
    for rec in events:
        fold_trace_event(cold, rec)
    live = empty_trace_stats()
    for rec in events:
        fold_trace_event(live, rec)
    assert public_trace_stats(live) == public_trace_stats(cold)
    assert public_trace_stats(cold)["n_llm_calls"] == 1
    assert public_trace_stats(cold)["n_tool_calls"] == 1
