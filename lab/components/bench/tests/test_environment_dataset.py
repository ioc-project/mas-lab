#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dataset item environment overlays the experiment world; it is not a second world."""

from __future__ import annotations

from types import SimpleNamespace

import jsonschema
import pytest
from mas.lab.benchmark.dataset import DatasetItem
from mas.lab.benchmark.environment.attach import blocks_for_run, digest_for_run
from mas.lab.inputs import load_run_input, run_input_to_dict, validate_run_input_envelope
from mas.lab.schemas.validate import validate_against_lab_schema


def _dataset(*, environment: dict | None = None, extra_item: dict | None = None) -> dict:
    item: dict = {
        "id": "case",
        "inputs": {"user": "Pay the fare three times."},
    }
    if environment is not None:
        item["environment"] = environment
    if extra_item:
        item.update(extra_item)
    return {
        "apiVersion": "lab/v1",
        "kind": "Dataset",
        "metadata": {"name": "revocation-queries"},
        "spec": {"items": [item]},
    }


def _trigger(*, occurrence: int = 2) -> dict:
    return {
        "id": "revoke-after-2",
        "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": occurrence},
        "do": [{"type": "http_call", "config": {"fixture": "issuer", "path": "/admin/revoke"}}],
    }


def test_dataset_schema_accepts_item_triggers() -> None:
    validate_against_lab_schema(
        "dataset",
        _dataset(environment={"triggers": [_trigger()]}),
    )


def test_dataset_schema_accepts_item_triggers_next_to_tool_fixtures() -> None:
    validate_against_lab_schema(
        "dataset",
        _dataset(
            environment={"triggers": [_trigger()]},
            extra_item={"inputs": {"user": "Pay.", "tool_fixtures": {"by_tool": {"*": {"ok": True}}}}},
        ),
    )


def test_dataset_schema_refuses_item_fixtures() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate_against_lab_schema(
            "dataset",
            _dataset(
                environment={
                    "fixtures": [{"name": "issuer", "kind": "process", "command": ["python3", "f.py"]}],
                }
            ),
        )


def test_run_input_schema_accepts_item_triggers() -> None:
    validate_against_lab_schema(
        "run-input",
        {
            "inputs": {"user": "Pay."},
            "environment": {"triggers": [_trigger()]},
        },
    )


def test_run_input_schema_refuses_item_fixtures() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate_against_lab_schema(
            "run-input",
            {
                "inputs": {"user": "Pay."},
                "environment": {
                    "fixtures": [{"name": "issuer", "kind": "process", "command": ["python3", "f.py"]}],
                },
            },
        )
    with pytest.raises(jsonschema.ValidationError):
        validate_run_input_envelope(
            {
                "inputs": {"user": "Pay."},
                "environment": {"fixtures": []},
            }
        )


def test_item_triggers_overlay_experiment_fixtures() -> None:
    exp = SimpleNamespace(
        environment={
            "clock": {"kind": "virtual"},
            "fixtures": [{"name": "issuer", "kind": "process", "command": ["python3", "fixtures/issuer.py"]}],
            "triggers": [_trigger(occurrence=1)],
        }
    )
    scenario = SimpleNamespace(environment={"clock": {"kind": "virtual", "start_s": 0}})
    item = {"id": "revoked_mid", "environment": {"triggers": [_trigger(occurrence=2)]}}

    merged = blocks_for_run(exp=exp, scenario=scenario, item=item)

    assert [row["name"] for row in merged["fixtures"]] == ["issuer"]
    assert merged["clock"] == {"kind": "virtual", "start_s": 0}
    assert [row["when"]["occurrence"] for row in merged["triggers"]] == [1, 2]


def test_item_digest_differs_when_triggers_differ() -> None:
    exp = SimpleNamespace(
        environment={"fixtures": [{"name": "issuer", "kind": "process", "command": ["python3", "f.py"]}]}
    )
    scenario = SimpleNamespace(environment={})
    a = {"id": "a", "environment": {"triggers": [_trigger(occurrence=2)]}}
    b = {"id": "b", "environment": {"triggers": [_trigger(occurrence=3)]}}
    assert digest_for_run(exp=exp, scenario=scenario, item=a) != digest_for_run(exp=exp, scenario=scenario, item=b)


def test_empty_item_fixtures_list_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot declare fixtures"):
        blocks_for_run(
            exp=None,
            scenario=None,
            item={"environment": {"fixtures": []}},
        )


def test_run_input_round_trip_preserves_environment() -> None:
    item = {
        "id": "revoked_mid",
        "inputs": {"user": "Pay the fare three times."},
        "environment": {"triggers": [_trigger()]},
    }
    run = load_run_input(item)
    envelope = run_input_to_dict(run)
    assert envelope["environment"]["triggers"][0]["id"] == "revoke-after-2"
    validate_run_input_envelope(envelope)
    reloaded = load_run_input({"id": "revoked_mid", **envelope})
    assert reloaded.environment == run.environment


def test_dataset_item_keeps_environment_out_of_inputs() -> None:
    data = {
        "id": "revoked_mid",
        "inputs": {"user": "Pay.", "tool_fixtures": {"by_tool": {"*": {"ok": True}}}},
        "environment": {"triggers": [_trigger()]},
        "category": "world",
    }
    item = DatasetItem.from_dict(data)
    assert item.run_input.environment["triggers"][0]["id"] == "revoke-after-2"
    dumped = item.to_dict()
    assert "environment" not in dumped.get("inputs", {})
    assert dumped["environment"]["triggers"][0]["id"] == "revoke-after-2"
    assert dumped["inputs"]["tool_fixtures"]["by_tool"]["*"]["ok"] is True
