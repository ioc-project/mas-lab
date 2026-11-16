#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``spec.control`` is checked by the layers that answer to its keys; ctl names none."""

from __future__ import annotations

import pytest
from mas.ctl.manifest.spec_bindings import SpecBindingError, parse_control


def test_a_valid_control_block_passes() -> None:
    parse_control(None)
    parse_control({"retry": {"llm": {"max_attempts": 9}, "tools": {"max_attempts": 1}}})
    parse_control({"circuit_breaker": {"failure_threshold": 2, "enabled": False}})
    parse_control({"circuit_breaker": None})


def test_an_unknown_retry_slot_is_refused() -> None:
    with pytest.raises(SpecBindingError, match="unknown field"):
        parse_control({"retry": {"llm": {}, "http": {}}})


def test_a_bad_param_is_refused_by_the_layer() -> None:
    with pytest.raises(SpecBindingError, match="max_attempts"):
        parse_control({"retry": {"llm": {"max_attempts": 0}}})


def test_a_control_key_must_be_an_object() -> None:
    with pytest.raises(SpecBindingError, match="must be an object"):
        parse_control({"retry": 3})
