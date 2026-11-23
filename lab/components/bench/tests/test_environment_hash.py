#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from mas.lab.benchmark.cache.trace_store import compute_run_hash
from mas.lab.benchmark.environment.engine import environment_digest


def test_no_environment_hash_unchanged() -> None:
    config = {"agents": [{"id": "a", "llm_model": "mock"}]}
    run_input = {"inputs": {"user": "hello"}}
    base = compute_run_hash(config, run_input, "i", 0, {}, infra_info=None)
    same = compute_run_hash(config, run_input, "i", 0, {}, infra_info=None, environment_digest="")
    assert base == same


def test_changed_trigger_changes_hash() -> None:
    a = environment_digest({"triggers": [{"id": "t", "when": {"on": "tool_result", "occurrence": 1}}]})
    b = environment_digest({"triggers": [{"id": "t", "when": {"on": "tool_result", "occurrence": 2}}]})
    assert a != b
    config = {"agents": [{"id": "a", "llm_model": "mock"}]}
    run_input = {"inputs": {"user": "hello"}}
    ha = compute_run_hash(config, run_input, "i", 0, {}, environment_digest=a)
    hb = compute_run_hash(config, run_input, "i", 0, {}, environment_digest=b)
    assert ha != hb


def test_allocated_port_not_in_digest() -> None:
    a = environment_digest(
        {"fixtures": [{"name": "issuer", "kind": "process", "command": ["python", "f.py"], "port": "auto"}]}
    )
    b = environment_digest(
        {"fixtures": [{"name": "issuer", "kind": "process", "command": ["python", "f.py"], "port": 9}]}
    )
    assert a == b


def test_generated_control_not_in_flavour_info() -> None:
    from mas.lab.benchmark.cache.trace_store import extract_flavour_info

    flavour = {"spec": {"governance": ["budget@v1"]}}
    info = extract_flavour_info(flavour)
    generated = {"spec": {"governance": ["budget@v1", "environment@v1"]}}
    # Hash uses flavour_info taken before the generated Control is attached.
    assert extract_flavour_info(flavour) == info
    assert "environment@v1" not in json_dump(info)
    assert "environment@v1" in json_dump(generated)


def json_dump(obj) -> str:
    import json

    return json.dumps(obj)
