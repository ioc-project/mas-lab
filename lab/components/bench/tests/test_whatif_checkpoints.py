# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.lab.benchmark.schedule.whatif import (
    apply_session_checkpoint_triggers,
    compile_checkpoint_trigger,
    consumer_gate,
    parse_resume_from,
    publish_named_checkpoints,
    published_checkpoint_path,
    validate_scenario_graph,
)


def test_compile_checkpoint_trigger_is_checkpoint_then_resume() -> None:
    trigger = compile_checkpoint_trigger(
        {
            "id": "after-pay-2",
            "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 2},
        }
    )
    assert trigger["id"] == "after-pay-2"
    assert trigger["when"]["occurrence"] == 2
    assert trigger["do"][0]["type"] == "checkpoint"
    assert trigger["do"][0]["config"]["persist"] is True
    assert trigger["do"][0]["config"]["auto_stop"] is True
    assert trigger["do"][1]["type"] == "resume"


def test_compile_checkpoint_trigger_pause_after() -> None:
    trigger = compile_checkpoint_trigger(
        {
            "id": "stop-here",
            "trigger": {"on": "tool_call", "tool": "pay_fare"},
            "after": "pause",
        }
    )
    assert trigger["do"][-1]["type"] == "pause"


def test_apply_session_checkpoint_triggers_writes_environment() -> None:
    producer = SimpleNamespace(
        id="producer",
        produce_checkpoints=["after-pay-2"],
        debug=None,
        environment={},
    )
    apply_session_checkpoint_triggers(
        [producer],
        [{"id": "after-pay-2", "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 2}}],
    )
    triggers = producer.environment["triggers"]
    assert triggers[0]["id"] == "after-pay-2"
    assert triggers[0]["do"][0]["type"] == "checkpoint"


def test_validate_scenario_graph_requires_owner() -> None:
    producer = SimpleNamespace(
        id="producer",
        depends_on=[],
        resume_from=None,
        produce_checkpoints=[],
    )
    fork = SimpleNamespace(
        id="fork",
        depends_on=["producer"],
        resume_from="after-pay-2",
        produce_checkpoints=[],
    )
    with pytest.raises(ValueError, match="produce_checkpoints owner"):
        validate_scenario_graph([producer, fork], [{"id": "after-pay-2"}])


def test_validate_scenario_graph_cycle() -> None:
    a = SimpleNamespace(id="a", depends_on=["b"], resume_from=None, produce_checkpoints=[])
    b = SimpleNamespace(id="b", depends_on=["a"], resume_from=None, produce_checkpoints=[])
    with pytest.raises(ValueError, match="cycle"):
        validate_scenario_graph([a, b])


def test_parse_resume_from_mapping() -> None:
    assert parse_resume_from("after-pay-2") == ("after-pay-2", 1)
    assert parse_resume_from({"id": "after-pay-2", "index": 2}) == ("after-pay-2", 2)


def test_consumer_gate_checkpoint() -> None:
    spec = SimpleNamespace(resume_from="after-pay-2", resume_index=1)
    assert consumer_gate(spec) == ("checkpoint", "after-pay-2", 1)


def test_publish_named_checkpoints(tmp_path: Path) -> None:
    run_dir = tmp_path / "run" / "checkpoints"
    run_dir.mkdir(parents=True)
    src = run_dir / "after-pay-2.checkpoint.json"
    src.write_bytes(b'{"ok": true}')
    published = publish_named_checkpoints(
        run_checkpoint_dir=run_dir,
        output_dir=tmp_path,
        names=["after-pay-2"],
        key="item1/r1",
    )
    dest = published_checkpoint_path(tmp_path, "after-pay-2", key="item1/r1")
    assert dest.is_file()
    assert dest in published or dest.read_bytes() == b'{"ok": true}'


def test_apply_session_checkpoint_triggers_is_idempotent() -> None:
    producer = SimpleNamespace(
        id="producer",
        produce_checkpoints=["after-pay-2"],
        debug=None,
        environment={},
    )
    catalog = [{"id": "after-pay-2", "when": {"on": "tool_result", "tool": "pay_fare", "occurrence": 2}}]
    apply_session_checkpoint_triggers([producer], catalog)
    apply_session_checkpoint_triggers([producer], catalog)
    assert len(producer.environment["triggers"]) == 1


def test_validate_scenario_graph_refuses_two_owners() -> None:
    a = SimpleNamespace(id="a", depends_on=[], resume_from=None, produce_checkpoints=["after-pay-2"])
    b = SimpleNamespace(id="b", depends_on=[], resume_from=None, produce_checkpoints=["after-pay-2"])
    with pytest.raises(ValueError, match="owned by"):
        validate_scenario_graph([a, b], [{"id": "after-pay-2"}])


def test_validate_scenario_graph_refuses_duplicate_catalog_ids() -> None:
    spec = SimpleNamespace(id="a", depends_on=[], resume_from=None, produce_checkpoints=[])
    with pytest.raises(ValueError, match="duplicate"):
        validate_scenario_graph([spec], [{"id": "x"}, {"id": "x"}])


def test_publish_does_not_treat_a_longer_name_as_a_shorter_one(tmp_path: Path) -> None:
    run_dir = tmp_path / "run" / "checkpoints"
    run_dir.mkdir(parents=True)
    (run_dir / "after-pay-2.checkpoint.json").write_bytes(b'{"ok": true}')
    published = publish_named_checkpoints(
        run_checkpoint_dir=run_dir,
        output_dir=tmp_path,
        names=["pay"],
        missing_ok=True,
        key="item1/r1",
    )
    assert published == []


def test_wait_dependencies_is_outside_the_parallel_slot() -> None:
    src = Path(__file__).resolve().parents[1] / "src/mas/lab/benchmark/schedule/run_batch/execute.py"
    text = src.read_text(encoding="utf-8")
    assert text.index("await _wait_dependencies") < text.index("async with _sem:")


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "", "x" * 80])
def test_checkpoint_names_cannot_leave_the_catalog(name: str) -> None:
    from types import SimpleNamespace

    from mas.lab.benchmark.schedule.whatif import validate_scenario_graph

    spec = SimpleNamespace(id="p", depends_on=[], resume_from=None, produce_checkpoints=[name])
    with pytest.raises(ValueError, match="checkpoint name|produce_checkpoints"):
        validate_scenario_graph([spec], [])
