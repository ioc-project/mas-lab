#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import pytest
from mas.runtime.spec.checkpoint import CheckpointPolicy, CheckpointSpecError, parse_checkpoint_policy
from mas.runtime.spec.parser import parse_agent_spec


def test_checkpoint_policy_defaults_preserve_existing_behavior() -> None:
    assert parse_checkpoint_policy(None) == CheckpointPolicy()
    parse_agent_spec({"checkpoint": {"mode": "none"}})


def test_checkpoint_policy_parses_cadence_and_retention() -> None:
    policy = parse_checkpoint_policy(
        {
            "mode": "on_event",
            "triggers": ["after_llm_call", "every_n_turns"],
            "every_n_turns": 4,
            "retention": {"mode": "last_n", "n": 7},
            "auto_resume_latest": True,
        }
    )

    assert policy.mode == "on_event"
    assert policy.triggers == ("after_llm_call", "every_n_turns")
    assert policy.retention_mode == "last_n"
    assert policy.retention_n == 7
    assert policy.auto_resume_latest is True


def test_checkpoint_storage_is_independent_of_cadence() -> None:
    memory = parse_checkpoint_policy({"mode": "every_turn", "storage": "memory"})
    assert memory.resolved_storage() == "memory"
    assert memory.uses_memory() is True
    assert memory.uses_disk() is False

    disk = parse_checkpoint_policy(
        {"mode": "every_turn", "storage": {"kind": "disk", "path": ".mas/ckpts"}}
    )
    assert disk.resolved_storage() == "disk"
    assert disk.storage_path == ".mas/ckpts"
    assert disk.uses_disk() is True

    hybrid = parse_checkpoint_policy({"mode": "every_turn", "storage": {"kind": "hybrid"}})
    assert hybrid.resolved_storage() == "hybrid"
    assert hybrid.uses_disk() is True
    assert hybrid.uses_memory() is True


def test_checkpoint_portability_aliases_deep_and_shallow() -> None:
    deep = parse_checkpoint_policy({"portability": "deep"})
    assert deep.portability == "self_contained"
    assert deep.is_deep() is True
    assert deep.is_shallow() is False
    shallow = parse_checkpoint_policy({"portability": "shallow"})
    assert shallow.portability == "reference"
    assert shallow.is_shallow() is True
    assert parse_checkpoint_policy({"portability": "self_contained"}).is_deep() is True


def test_checkpoint_storage_aliases_legacy_mode() -> None:
    assert parse_checkpoint_policy({"mode": "in_memory"}).resolved_storage() == "memory"
    assert parse_checkpoint_policy({"mode": "every_turn"}).resolved_storage() == "disk"
    assert parse_checkpoint_policy({"mode": "none"}).resolved_storage() == "none"


@pytest.mark.parametrize(
    "raw",
    [
        {"mode": "unknown"},
        {"triggers": ["not-an-event"]},
        {"every_n_turns": True},
        {"retention": {"mode": "last_n", "n": 0}},
        {"auto_resume_latest": "true"},
        {"mode": "in_memory", "auto_resume_latest": True},
        {"storage": "tape"},
        {"storage": {"kind": "disk", "backend": "s3"}},
        {"unexpected": True},
    ],
)
def test_checkpoint_policy_rejects_malformed_values(raw: dict) -> None:
    with pytest.raises(CheckpointSpecError):
        parse_agent_spec({"checkpoint": raw})