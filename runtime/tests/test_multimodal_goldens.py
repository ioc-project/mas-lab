#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Text-only cache keys, previews and run hashes stay byte-identical."""

from __future__ import annotations

import json

from mas.lab.benchmark.cache.trace_store import compute_run_hash
from mas.lab.inputs import fingerprint_run_input, load_run_input, run_input_to_dict
from mas.runtime.engine.exchange_preview import format_llm_messages
from mas.runtime.engine.llm_cache import llm_cache_key

# Recorded on feat/catalog-modalities (6adfd94d) before any multimodal change.
GOLDEN_CACHE_KEY = "78e320f1a1169a559e97b08efe90b97e32dcef4f52e10af19ce52414ee0afe43"
GOLDEN_PREVIEW = "[user]\n  hello"
GOLDEN_RUN_INPUT_DICT = {"inputs": {"user": "hello"}}
GOLDEN_FINGERPRINT = "e4279e0e63537dde98789c65b2c53c2bda1f3652d09caaac9cb0b176809340a4"
GOLDEN_RUN_HASH = "35e89fcfd59710028da0"
GOLDEN_LIST_USER_DICT = {"inputs": {"user": ["First", "Second"]}}


def test_text_only_llm_cache_key_matches_base_commit():
    key = llm_cache_key("gpt-4o", [{"role": "user", "content": "hello"}], None)
    assert key == GOLDEN_CACHE_KEY


def test_text_only_format_llm_messages_matches_base_commit():
    assert format_llm_messages([{"role": "user", "content": "hello"}]) == GOLDEN_PREVIEW


def test_text_only_parts_authoring_matches_string_goldens():
    run = load_run_input({"id": "t", "inputs": {"user": {"parts": [{"text": "hello"}]}}})
    assert isinstance(run.user[0]["content"], str)
    assert run_input_to_dict(run) == GOLDEN_RUN_INPUT_DICT
    assert fingerprint_run_input(run) == GOLDEN_FINGERPRINT


def test_text_only_run_input_dict_and_fingerprint_match_base_commit():
    run = load_run_input({"id": "t", "inputs": {"user": "hello"}})
    payload = run_input_to_dict(run)
    assert payload == GOLDEN_RUN_INPUT_DICT
    assert json.dumps(payload, sort_keys=True, separators=(",", ":")) == json.dumps(
        GOLDEN_RUN_INPUT_DICT, sort_keys=True, separators=(",", ":")
    )
    assert fingerprint_run_input(run) == GOLDEN_FINGERPRINT


def test_text_only_run_hash_matches_base_commit():
    run = load_run_input({"id": "t", "inputs": {"user": "hello"}})
    digest = compute_run_hash(
        config={"agents": [{"id": "a", "llm_model": "gpt-4o"}]},
        run_input=run_input_to_dict(run),
        item_id="t",
        run_idx=0,
        flavour_info={},
    )
    assert digest == GOLDEN_RUN_HASH


def test_list_of_strings_run_input_dict_matches_base_commit():
    run = load_run_input({"id": "m", "inputs": {"user": ["First", "Second"]}})
    assert run_input_to_dict(run) == GOLDEN_LIST_USER_DICT
