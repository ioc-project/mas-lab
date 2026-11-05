#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from mas.lab.benchmark.schedule.run_batch.finalize import write_session_mappings
from mas.lab.benchmark.schedule.run_batch.load import _cross_checkpoint_axis
from mas.lab.lab.config.mas_experiment import CheckpointAxisEntry, _parse_checkpoint_axis


def test_checkpoint_axis_defaults_to_fresh_start_and_resolves_relative_paths(tmp_path) -> None:
    assert _parse_checkpoint_axis(None, tmp_path) == [CheckpointAxisEntry(id="none")]
    assert _parse_checkpoint_axis(
        [{"id": "incident", "path": "checkpoints/incident.json"}],
        tmp_path,
    ) == [CheckpointAxisEntry(id="incident", path=tmp_path / "checkpoints/incident.json")]


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [{"id": "none", "path": "unexpected.json"}],
        [{"id": "bad/id", "path": "checkpoint.json"}],
        [{"id": "missing-path"}],
        [{"id": "same", "path": "a.json"}, {"id": "same", "path": "b.json"}],
    ],
)
def test_checkpoint_axis_rejects_invalid_entries(raw, tmp_path) -> None:
    with pytest.raises(ValueError):
        _parse_checkpoint_axis(raw, tmp_path)


def test_omitted_and_single_none_axes_preserve_item_identity() -> None:
    items = [{"id": "item-1", "inputs": {"user": "prompt"}}]

    assert _cross_checkpoint_axis(items, [CheckpointAxisEntry("none")], explicit=False) is items
    assert _cross_checkpoint_axis(items, [CheckpointAxisEntry("none")], explicit=True) is items


def test_checkpoint_axis_crosses_items_and_overrides_item_checkpoint(caplog) -> None:
    items = [
        {
            "id": "item-1",
            "inputs": {"user": "prompt", "checkpoint": {"load": "item.json"}},
        }
    ]
    axis = [
        CheckpointAxisEntry("none"),
        CheckpointAxisEntry("saved", Path("/saved/checkpoint.json")),
    ]

    expanded = _cross_checkpoint_axis(items, axis, explicit=True)

    assert len(expanded) == 2
    assert expanded[0]["id"] == "item-1--checkpoint-none"
    assert expanded[0]["source_item_id"] == "item-1"
    assert "checkpoint" not in expanded[0]["inputs"]
    assert expanded[1]["inputs"]["checkpoint"]["load"] == "/saved/checkpoint.json"
    assert [item["checkpoint_id"] for item in expanded] == ["", "saved"]
    assert "overrides inputs.checkpoint" in caplog.text


def test_session_mapping_artifact_includes_checkpoint_lineage(tmp_path) -> None:
    write_session_mappings(
        tmp_path,
        [
            {
                "experiment_id": "what-if",
                "scenario": "react",
                "item_id": "case-a",
                "run": 2,
                "session_id": "session-2",
                "checkpoint_id": "incident",
                "forked_from_checkpoint": "/checkpoints/incident.json",
            }
        ],
    )

    import json

    mapping = json.loads((tmp_path / "session_mappings.jsonl").read_text().strip())
    assert mapping == {
        "experiment_id": "what-if",
        "scenario_id": "react",
        "item_id": "case-a",
        "run_idx": 2,
        "session_id": "session-2",
        "checkpoint_id": "incident",
        "forked_from_checkpoint": "/checkpoints/incident.json",
    }
