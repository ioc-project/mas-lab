#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Unit tests for the golden-run events normalization/comparison sort.

Concurrent agents each get their own async plugin-dispatch worker thread, so
two runs of the *same* logical trace can interleave different agents'
event streams in a different order. ``normalize_events_lines`` sorts events
to cancel this out before comparison; these tests exercise that sort
directly, without spinning up a real benchmark run.
"""

from __future__ import annotations

import json
from pathlib import Path

from mas.lab.benchmark.golden.events import compare_events_files, normalize_events_lines


def _write(tmp_path: Path, name: str, lines: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return path


def test_cross_agent_interleaving_order_does_not_affect_normalization() -> None:
    """Two runs with the same events in a different cross-agent order
    normalize identically — this is the benign async-writer race."""
    a = [
        {"kind": "execution_start", "agent_id": "moderator", "timestamp": 1.0},
        {"kind": "execution_start", "agent_id": "schedule_agent", "timestamp": 1.1},
        {"kind": "execution_end", "agent_id": "moderator", "timestamp": 1.2},
    ]
    b = [
        {"kind": "execution_start", "agent_id": "schedule_agent", "timestamp": 2.1},
        {"kind": "execution_start", "agent_id": "moderator", "timestamp": 2.0},
        {"kind": "execution_end", "agent_id": "moderator", "timestamp": 2.2},
    ]
    norm_a = normalize_events_lines(json.dumps(e) for e in a)
    norm_b = normalize_events_lines(json.dumps(e) for e in b)
    assert norm_a == norm_b


def test_mistagged_generic_agent_id_disambiguated_by_content() -> None:
    """Some event kinds don't carry their real emitting agent's id and fall
    back to a shared generic tag — several real agents' events then land in
    the same nominal agent_id group with nothing left to order by except
    their own content. The sort must still converge on the same result
    regardless of which one happened to be written first."""
    a = [
        {"kind": "context_part_contributed", "agent_id": "agent", "content_preview": "moderator role"},
        {"kind": "context_part_contributed", "agent_id": "agent", "content_preview": "schedule_agent role"},
    ]
    b = [
        {"kind": "context_part_contributed", "agent_id": "agent", "content_preview": "schedule_agent role"},
        {"kind": "context_part_contributed", "agent_id": "agent", "content_preview": "moderator role"},
    ]
    norm_a = normalize_events_lines(json.dumps(e) for e in a)
    norm_b = normalize_events_lines(json.dumps(e) for e in b)
    assert norm_a == norm_b


def test_genuinely_different_content_still_detected_as_mismatch(tmp_path) -> None:
    """The sort must not paper over a real content difference."""
    actual = _write(
        tmp_path,
        "actual.jsonl",
        [
            {"kind": "execution_start", "agent_id": "moderator", "input": "hi"},
        ],
    )
    expected = _write(
        tmp_path,
        "expected.jsonl",
        [
            {"kind": "execution_start", "agent_id": "moderator", "input": "bye"},
        ],
    )
    match, diff = compare_events_files(actual, expected)
    assert not match
    assert "changed_keys=" in diff
    assert "input" in diff


def test_identical_multiset_in_different_order_matches(tmp_path) -> None:
    actual = _write(
        tmp_path,
        "actual.jsonl",
        [
            {"kind": "execution_start", "agent_id": "schedule_agent", "input": "a"},
            {"kind": "execution_start", "agent_id": "moderator", "input": "b"},
        ],
    )
    expected = _write(
        tmp_path,
        "expected.jsonl",
        [
            {"kind": "execution_start", "agent_id": "moderator", "input": "b"},
            {"kind": "execution_start", "agent_id": "schedule_agent", "input": "a"},
        ],
    )
    match, diff = compare_events_files(actual, expected)
    assert match, diff


def test_parity_diff_names_extra_keys(tmp_path) -> None:
    actual = _write(
        tmp_path,
        "actual.jsonl",
        [
            {"kind": "context_assembled", "agent_id": "qa-agent", "tools": ["calc"]},
        ],
    )
    expected = _write(
        tmp_path,
        "expected.jsonl",
        [
            {"kind": "context_assembled", "agent_id": "qa-agent"},
        ],
    )
    match, diff = compare_events_files(actual, expected)
    assert not match
    assert "extra_keys=['tools']" in diff
