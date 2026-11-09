#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for Run Input Envelope (mas.lab.inputs)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.lab.inputs import (
    RunInput,
    load_run_input,
    run_input_to_dict,
    validate_run_input_envelope,
)


def test_user_txt_ref_is_raw_text(tmp_path: Path):
    (tmp_path / "prompt.txt").write_text("Note: colons: are fine\n", encoding="utf-8")
    run = load_run_input(
        {
            "id": "t",
            "inputs": {"user": {"ref": "prompt.txt"}},
        },
        base_path=tmp_path,
    )
    assert run.primary_prompt == "Note: colons: are fine"


def test_user_list_of_strings():
    run = load_run_input(
        {
            "id": "m",
            "inputs": {"user": ["First", "Second"]},
        }
    )
    assert run.scripted_queries() == ["First", "Second"]
    assert run_input_to_dict(run)["inputs"]["user"] == ["First", "Second"]


def test_user_ref_object_and_expectations_file(tmp_path: Path):
    (tmp_path / "prompt.yaml").write_text("From file\n", encoding="utf-8")
    (tmp_path / "gt.yaml").write_text(
        yaml.dump({"ground_truth": "Paris"}),
        encoding="utf-8",
    )
    item = {
        "id": "r",
        "inputs": {"user": {"ref": "prompt.yaml"}},
        "expectations": {"ref": "gt.yaml"},
    }
    run = load_run_input(item, base_path=tmp_path)
    assert run.primary_prompt == "From file"
    assert run.expectations["ground_truth"] == "Paris"


def test_catalog_id_ref_not_resolved():
    with pytest.raises(ValueError, match="not resolved yet"):
        load_run_input(
            {
                "id": "x",
                "inputs": {"user": {"id": "example-library:prompts@v1#a"}},
            }
        )


def test_memory_seed_kind_file(tmp_path: Path):
    (tmp_path / "seeds.yaml").write_text(
        yaml.dump(
            {
                "apiVersion": "memory-seed/v1",
                "kind": "MemorySeed",
                "entries": [{"key": "policy", "content": "economy only"}],
            }
        ),
        encoding="utf-8",
    )
    run = load_run_input(
        {
            "id": "s",
            "inputs": {
                "user": "Q",
                "memory_seeds": "seeds.yaml",
            },
        },
        base_path=tmp_path,
    )
    assert run.memory_seeds[0]["source"] == "policy"
    assert run.memory_seeds[0]["content"] == "economy only"


def test_validate_user_string_envelope():
    pytest.importorskip("jsonschema")
    validate_run_input_envelope({"inputs": {"user": "Hello"}})


def test_load_run_input_envelope_item():
    item = {
        "id": "002",
        "inputs": {
            "user": "Plan a trip",
            "tool_fixtures": {"by_tool": {"*": {"routes": []}}},
        },
        "expectations": {
            "ground_truth": "PolicyDenial",
            "details": {"governance": {"expected": "guardrail_triggered"}},
        },
    }
    run = load_run_input(item)
    assert run.primary_prompt == "Plan a trip"
    assert run.tool_fixtures == {"by_tool": {"*": {"routes": []}}}
    assert run.expectations["details"]["governance"]["expected"] == "guardrail_triggered"


def test_load_run_input_empty_item_still_loads():
    run = load_run_input({"id": "001"})
    assert run.primary_prompt == ""


def test_load_run_input_empty_user_list_still_loads():
    run = load_run_input({"id": "001", "inputs": {"user": []}})
    assert run.primary_prompt == ""


def test_legacy_query_item_still_loads():
    run = load_run_input({"id": "001", "query": "what is up?"})
    assert run.primary_prompt == "what is up?"


def test_legacy_prompt_item_still_loads():
    run = load_run_input(
        {
            "id": "001",
            "prompt": "legacy prompt",
            "expected_answer": "4",
            "memory_seeds": [{"source": "s", "content": "seed"}],
        }
    )
    assert run.primary_prompt == "legacy prompt"
    assert run.expectations["ground_truth"] == "4"
    assert run.memory_seeds[0]["source"] == "s"
    assert run.memory_seeds[0]["content"] == "seed"


def test_legacy_role_list_still_loads():
    run = load_run_input(
        {
            "id": "legacy",
            "inputs": {"user": [{"role": "user", "content": "Old shape"}]},
        }
    )
    assert run.primary_prompt == "Old shape"


def test_legacy_expectations_correct_action_folds_into_details(caplog):
    import logging

    from mas.lab.deprecations import clear_deprecation_warnings

    clear_deprecation_warnings()
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        run = load_run_input(
            {
                "id": "routing-policy-rollback",
                "inputs": {"user": "Triage the edge-gateway regression."},
                "expectations": {"correct_action": {"service": "edge-gateway", "action": "rollback"}},
            }
        )
    assert run.expectations["details"]["correct_action"] == {
        "service": "edge-gateway",
        "action": "rollback",
    }
    assert "dataset.legacy_expectations" in caplog.text


def test_canonical_details_correct_action_does_not_warn(caplog):
    import logging

    from mas.lab.deprecations import clear_deprecation_warnings

    clear_deprecation_warnings()
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        run = load_run_input(
            {
                "id": "routing-policy-rollback",
                "inputs": {"user": "Triage the edge-gateway regression."},
                "expectations": {
                    "details": {
                        "correct_action": {
                            "service": "edge-gateway",
                            "action": "rollback",
                        }
                    }
                },
            }
        )
    assert run.expectations["details"]["correct_action"]["action"] == "rollback"
    assert "dataset.legacy_expectations" not in caplog.text


def test_legacy_expectations_clash_with_details_raises():
    with pytest.raises(ValueError, match="already exist in details"):
        load_run_input(
            {
                "id": "clash",
                "inputs": {"user": "Q"},
                "expectations": {
                    "correct_action": {"action": "restart"},
                    "details": {"correct_action": {"action": "rollback"}},
                },
            }
        )


def test_user_string_is_not_a_path(tmp_path: Path):
    run = load_run_input(
        {
            "id": "t",
            "inputs": {"user": "not-a-file.yaml"},
        },
        base_path=tmp_path,
    )
    assert run.primary_prompt == "not-a-file.yaml"


def test_load_run_input_scenario_inputs_merge():
    item = {
        "id": "003",
        "inputs": {"user": "Q"},
    }
    scenario = {
        "inputs": {"memory_seeds": [{"source": "s", "content": "seed"}]},
        "expectations": {"ground_truth": "42"},
    }
    run = load_run_input(item, scenario=scenario)
    assert run.memory_seeds == [{"source": "s", "content": "seed"}]
    assert run.expectations["ground_truth"] == "42"


def test_load_run_input_scenario_memory_seed_merge():
    item = {
        "id": "003",
        "inputs": {"user": "Q"},
    }
    scenario = {"spec": {"memory_seed": [{"source": "s", "content": "seed"}]}}
    run = load_run_input(item, scenario=scenario)
    assert run.memory_seeds == [{"source": "s", "content": "seed"}]


def test_load_run_input_memory_seeds_from_file(tmp_path: Path):
    seed_file = tmp_path / "seeds.yaml"
    seed_file.write_text(
        yaml.dump([{"source": "file", "content": "from disk"}]),
        encoding="utf-8",
    )
    item = {
        "id": "004",
        "inputs": {
            "user": "Q",
            "memory_seeds": "seeds.yaml",
        },
    }
    run = load_run_input(item, base_path=tmp_path)
    assert run.memory_seeds == [{"source": "file", "content": "from disk"}]


def test_scripted_queries_multi_turn():
    run = RunInput(
        user=[
            {"role": "user", "content": "First"},
            {"role": "user", "content": "Second"},
        ],
        hitl=[{"role": "hitl", "content": "Operator fix"}],
    )
    assert run.scripted_queries() == ["First", "Second", "Operator fix"]


def test_run_input_round_trip_dict():
    run = RunInput(
        user=[{"role": "user", "content": "Hi"}],
        hitl=[{"role": "hitl", "content": "OK"}],
        expectations={"ground_truth": "x"},
    )
    envelope = run_input_to_dict(run)
    assert envelope["inputs"]["user"] == "Hi"
    assert envelope["inputs"]["hitl"] == ["OK"]
    envelope = run_input_to_dict(run)
    pytest.importorskip("jsonschema")
    validate_run_input_envelope(envelope)
    reloaded = load_run_input({"id": "r", **envelope})
    assert reloaded.primary_prompt == "Hi"
    assert reloaded.hitl[0]["content"] == "OK"


def test_tool_fixtures_path_and_fragment(tmp_path: Path):
    body = tmp_path / "tool_fixtures.yaml"
    body.write_text(
        yaml.dump(
            {
                "items": [
                    {"id": "a", "services": {"svc": {}}},
                    {"id": "b", "services": {"other": {}}},
                ]
            }
        ),
        encoding="utf-8",
    )
    item = {
        "id": "x",
        "inputs": {
            "user": "Q",
            "tool_fixtures": "tool_fixtures.yaml#a",
        },
        "expectations": {"details": {"action": "rollback"}},
    }
    run = load_run_input(item, base_path=tmp_path)
    shared = run.tool_fixtures["by_tool"]["*"]
    assert shared["id"] == "a"
    assert "svc" in shared["services"]
    assert run.expectations["details"]["action"] == "rollback"


def test_tool_fixtures_fragment_falls_back_to_next_candidate_key(tmp_path: Path):
    """A miss on one candidate list key (``items``) must fall through to the
    next (``fixtures``) instead of raising immediately."""
    body = tmp_path / "tool_fixtures.yaml"
    body.write_text(
        yaml.dump(
            {
                "items": [{"id": "other", "services": {}}],
                "fixtures": [{"id": "target", "services": {"svc": {}}}],
            }
        ),
        encoding="utf-8",
    )
    item = {
        "id": "x",
        "inputs": {
            "user": "Q",
            "tool_fixtures": "tool_fixtures.yaml#target",
        },
    }
    run = load_run_input(item, base_path=tmp_path)
    assert run.tool_fixtures["by_tool"]["*"]["id"] == "target"
    assert "svc" in run.tool_fixtures["by_tool"]["*"]["services"]


def test_tool_fixtures_by_tool_mapping(tmp_path: Path):
    (tmp_path / "celestia-weekend.yaml").write_text(
        yaml.dump({"id": "celestia-weekend", "services": {"celestia-inn": {}}}),
        encoding="utf-8",
    )
    item = {
        "id": "x",
        "inputs": {
            "user": "Q",
            "tool_fixtures": {
                "by_tool": {
                    "*": "celestia-weekend.yaml",
                    "query_db": {"rows": []},
                }
            },
        },
    }
    run = load_run_input(item, base_path=tmp_path)
    assert run.tool_fixtures["by_tool"]["*"]["id"] == "celestia-weekend"
    assert run.tool_fixtures["by_tool"]["query_db"] == {"rows": []}


def test_tool_fixtures_rejects_inline_payload_outside_by_tool(tmp_path: Path):
    with pytest.raises(ValueError, match="by_tool"):
        load_run_input(
            {"id": "x", "inputs": {"user": "Q", "tool_fixtures": {"services": {}}}},
            base_path=tmp_path,
        )


def test_tool_fixtures_rejects_unknown_mapping_keys(tmp_path: Path):
    with pytest.raises(ValueError, match="by_tool"):
        load_run_input(
            {"id": "x", "inputs": {"user": "Q", "tool_fixtures": {"custom_fixture": "a.yaml"}}},
            base_path=tmp_path,
        )


def test_scenario_params_are_not_tool_fixtures(tmp_path: Path):
    run = load_run_input(
        {"id": "x", "inputs": {"user": "Q"}},
        scenario={"spec": {"patch": {"params": {"fixture": "celestia-weekend.yaml"}}}},
        base_path=tmp_path,
    )
    assert run.tool_fixtures is None


def test_expectations_fold_app_keys_outside_details(tmp_path: Path, caplog):
    import logging

    from mas.lab.deprecations import clear_deprecation_warnings

    clear_deprecation_warnings()
    with caplog.at_level(logging.WARNING, logger="mas.lab.deprecations"):
        run = load_run_input(
            {
                "id": "x",
                "inputs": {"user": "Q"},
                "expectations": {"ground_truth": "42", "custom_check": {"k": 1}},
            },
            base_path=tmp_path,
        )
    assert run.expectations["ground_truth"] == "42"
    assert run.expectations["details"]["custom_check"] == {"k": 1}
    assert "dataset.legacy_expectations" in caplog.text


def test_tool_fixtures_binding_list(tmp_path: Path):
    (tmp_path / "metrics.yaml").write_text(
        yaml.dump({"id": "m", "services": {"celestia-inn": {}}}),
        encoding="utf-8",
    )
    item = {
        "id": "x",
        "inputs": {
            "user": "Q",
            "tool_fixtures": [
                {"tool": "get_metrics", "ref": "metrics.yaml"},
                {"tools": ["get_logs"], "ref": "metrics.yaml"},
            ],
        },
    }
    run = load_run_input(item, base_path=tmp_path)
    assert run.tool_fixtures["by_tool"]["get_metrics"]["id"] == "m"
    assert run.tool_fixtures["by_tool"]["get_logs"]["id"] == "m"


def test_tool_fixtures_by_tool_ref_and_inline_payload(tmp_path: Path):
    (tmp_path / "fixture.yaml").write_text("id: f\nservices: {}\n", encoding="utf-8")
    run = load_run_input(
        {
            "id": "y",
            "inputs": {
                "user": "Q",
                "tool_fixtures": {
                    "by_tool": {"query_db": {"ref": "fixture.yaml"}, "get_weather": {"data": "sunny"}},
                },
            },
        },
        base_path=tmp_path,
    )
    assert run.tool_fixtures["by_tool"]["query_db"]["id"] == "f"
    assert run.tool_fixtures["by_tool"]["get_weather"] == {"data": "sunny"}


def test_tool_fixtures_missing_ref_is_an_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        load_run_input(
            {"id": "x", "inputs": {"user": "Q", "tool_fixtures": "missing.yaml"}},
            base_path=tmp_path,
        )


def test_tool_fixtures_binding_requires_ref(tmp_path: Path):
    with pytest.raises(TypeError, match="ref"):
        load_run_input(
            {"id": "x", "inputs": {"user": "Q", "tool_fixtures": [{"tool": "t", "data": "a.yaml"}]}},
            base_path=tmp_path,
        )
