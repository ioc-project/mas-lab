#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.library.lab.steps.extract.trace_stats import empty_trace_stats, fold_trace_event, public_trace_stats


def test_fold_trace_event_tokens_and_cached() -> None:
    stats = empty_trace_stats()
    fold_trace_event(stats, {"kind": "llm_call_start", "timestamp": 10.0})
    fold_trace_event(
        stats,
        {
            "kind": "llm_call_end",
            "timestamp": 12.5,
            "tokens_used": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 40},
                "cache_read_input_tokens": 40,
            },
        },
    )
    fold_trace_event(stats, {"kind": "tool_call_start", "timestamp": 13.0})
    out = public_trace_stats(stats)
    assert out["duration_s"] == 3.0
    assert out["n_llm_calls"] == 1
    assert out["n_tool_calls"] == 1
    assert out["prompt_tokens"] == 100
    assert out["completion_tokens"] == 20
    assert out["total_tokens"] == 120
    assert out["cached_tokens"] == 40
