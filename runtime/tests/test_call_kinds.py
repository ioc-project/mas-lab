#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""normalize_call_kinds and leftover YAML key aliases."""

from __future__ import annotations

import warnings

import pytest
from mas.runtime.engine.call_kinds import (
    call_kind_matches,
    normalize_call_kinds,
    op_kind,
    read_aliased_field,
    reset_alias_warnings,
)


def test_normalize_call_kinds_maps_engine_ops() -> None:
    assert normalize_call_kinds("LLM_CALL") == ("llm",)
    assert normalize_call_kinds(["TOOL_HTTP", "llm"]) == ("tool", "llm")
    assert normalize_call_kinds(None) == ()
    assert op_kind("LLM_CALL") == "llm"
    assert op_kind("TOOL_CALL") == "tool"
    assert call_kind_matches("LLM_CALL", ("llm",))
    assert not call_kind_matches("TOOL_CALL", ("llm",))
    assert not call_kind_matches("LLM_CALL", ())
    assert not call_kind_matches("TOOL_CALL", ())


def test_mixed_for_and_applies_to_is_an_error() -> None:
    with pytest.raises(ValueError, match="mix"):
        read_aliased_field(
            {"for": "llm", "applies_to": ["LLM_CALL"]},
            "for",
            "applies_to",
            where="cache.yaml",
        )


def test_applies_to_alias_warns_and_reads_for() -> None:
    reset_alias_warnings()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = read_aliased_field(
            {"applies_to": ["LLM_CALL"]},
            "for",
            "applies_to",
            where="cache.yaml",
        )
    assert value == ["LLM_CALL"]
    assert caught
    assert "applies_to" in str(caught[0].message)
    assert "for" in str(caught[0].message)
