#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""With no identity layer named, the bytes that leave for MCP, A2A HTTP and an LLM vendor layer are today's.

The goldens were recorded from the tree before the identity layer existed. A stack without the layer
must stay byte-identical: this is the "absent layer, no effect" promise as a test that can go red.
"""

from __future__ import annotations

import pytest
from wiring_scenarios import SCENARIOS, load_golden


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_requests_without_an_identity_layer_equal_the_golden(name: str) -> None:
    assert await SCENARIOS[name]() == load_golden(name)
