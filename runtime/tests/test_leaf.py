#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""leaf_engine unwraps real middleware, not MagicMock auto-created attrs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mas.runtime.engine.leaf import leaf_engine


def test_leaf_engine_unwraps_inner():
    inner = SimpleNamespace(model="gpt-4o")
    outer = SimpleNamespace(inner=inner)
    assert leaf_engine(outer) is inner
    assert leaf_engine(inner) is inner
    assert leaf_engine(None) is None


def test_leaf_engine_does_not_follow_mock_inner():
    engine = MagicMock()
    assert leaf_engine(engine) is engine
    assert "inner" not in engine._mock_children


def test_leaf_engine_unwraps_normal_stack_beyond_eight_layers():
    leaf = SimpleNamespace()
    cur: object = leaf
    chain = [leaf]
    for _ in range(10):
        cur = SimpleNamespace(inner=cur)
        chain.append(cur)
    assert leaf_engine(chain[10]) is leaf


def test_leaf_engine_raises_after_unwrap_bound():
    leaf = SimpleNamespace()
    cur: object = leaf
    for _ in range(51):
        cur = SimpleNamespace(inner=cur)
    with pytest.raises(RuntimeError, match="exceeded 50 levels"):
        leaf_engine(cur)
