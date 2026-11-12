#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Default retry must not enter the hashed infra pipeline of shipped tutorials."""

from __future__ import annotations

from pathlib import Path

import yaml
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.compose.runner import infra_cache_identity
from mas.lab.benchmark.cache.trace_store import compute_run_hash
from mas.runtime.layers.compose import layer_id
from mas.runtime.layers.defaults import merge_default_retry

_REPO = Path(__file__).resolve().parents[1]


def test_default_retry_is_not_in_infra_cache_identity() -> None:
    resolved = ResolvedInfra(refs=["standard:openai"], llm_proxy={"api_base": "http://x", "pipeline": []})
    ident = infra_cache_identity(resolved)
    pipeline = ident["llm_proxy"].get("pipeline") or []
    assert all(layer_id(step) != "retry" for step in pipeline if isinstance(step, dict))


def test_wrap_time_retry_does_not_change_compute_run_hash() -> None:
    config = {"agents": [{"id": "a", "llm_model": "m"}]}
    run_input = {"inputs": {"user": "hello"}}
    empty = compute_run_hash(config, run_input, "i", 0, {}, infra_info={"llm_proxy": {"pipeline": []}})
    wrapped = merge_default_retry([], "llm")
    after = compute_run_hash(config, run_input, "i", 0, {}, infra_info={"llm_proxy": {"pipeline": []}})
    assert empty == after
    assert wrapped


def test_shipped_tutorial_hashes_stable_across_empty_infra() -> None:
    """Same tutorial bodies hash the same; wrap-time retry is not in infra_info."""
    hashes = []
    for path in sorted((_REPO / "docs" / "tutorials").glob("*/mas.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = {"agents": [{"id": "a", "llm_model": "m"}], "raw": raw}
        run_input = {"inputs": {"user": "hello"}}
        hashes.append(compute_run_hash(config, run_input, str(path), 0, {}, infra_info={"llm_proxy": {"pipeline": []}}))
    assert hashes
    again = []
    for path in sorted((_REPO / "docs" / "tutorials").glob("*/mas.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = {"agents": [{"id": "a", "llm_model": "m"}], "raw": raw}
        run_input = {"inputs": {"user": "hello"}}
        again.append(compute_run_hash(config, run_input, str(path), 0, {}, infra_info={"llm_proxy": {"pipeline": []}}))
    assert hashes == again
