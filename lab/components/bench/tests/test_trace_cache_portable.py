#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``trace_cache_portable``: a published cache must hit whatever LLM endpoint the reader uses."""

from __future__ import annotations

from mas.lab.benchmark.cache.trace_store import compute_run_hash
from mas.lab.lab.config.experiment_base import MASRunBase


def test_key_without_infra_is_stable_and_input_sensitive() -> None:
    config = {"agents": [{"id": "a", "llm_model": "m"}]}
    run_input = {"inputs": {"user": "hello"}}
    base = compute_run_hash(config, run_input, "i", 0, {}, infra_info=None)
    assert base == compute_run_hash(config, run_input, "i", 0, {}, infra_info=None)
    assert base != compute_run_hash(config, {"inputs": {"user": "hello!"}}, "i", 0, {}, infra_info=None)
    assert base != compute_run_hash(config, run_input, "i", 1, {}, infra_info=None)
    other_model = {"agents": [{"id": "a", "llm_model": "m2"}]}
    assert base != compute_run_hash(other_model, run_input, "i", 0, {}, infra_info=None)


def test_key_with_infra_depends_on_the_endpoint() -> None:
    config = {"agents": [{"id": "a", "llm_model": "m"}]}
    run_input = {"inputs": {"user": "hello"}}
    a = compute_run_hash(config, run_input, "i", 0, {}, infra_info={"llm_proxy": {"api_base": "http://a"}})
    b = compute_run_hash(config, run_input, "i", 0, {}, infra_info={"llm_proxy": {"api_base": "http://b"}})
    assert a != b


def test_flag_defaults_to_off() -> None:
    assert MASRunBase.__dataclass_fields__["trace_cache_portable"].default is False


def test_an_infra_override_changes_the_key_and_a_portable_cache_ignores_it() -> None:
    # Kills: a changed `--override .infra...` replaying the traces of the old mapping.
    from mas.lab.benchmark.cache.trace_store import with_infra_overrides

    config = {"agents": [{"id": "a", "llm_model": "m"}]}
    run_input = {"inputs": {"user": "hello"}}
    infra = {"llm_proxy": {"api_base": "http://a"}}
    gpt4o = with_infra_overrides(infra, [".infra.spec.models.mappings.m=azure/gpt-4o"])
    mini = with_infra_overrides(infra, [".infra.spec.models.mappings.m=azure/gpt-4o-mini"])
    plain = with_infra_overrides(infra, [".mas.agent[a].spec.models[0].id=x"])
    key = lambda i: compute_run_hash(config, run_input, "i", 0, {}, infra_info=i)  # noqa: E731
    assert key(gpt4o) != key(mini) and key(plain) == key(infra)  # only `.infra` overrides count
    assert with_infra_overrides(None, [".infra.spec.models.mappings.m=x"]) is None  # portable stays portable
