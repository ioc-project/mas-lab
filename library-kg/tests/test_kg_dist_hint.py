#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.library.kg._dist import extra_hint


def test_kg_extra_hint_names_this_library_wheel() -> None:
    hint = extra_hint("neo4j")
    assert "uv pip install" in hint
    assert "[neo4j]" in hint
