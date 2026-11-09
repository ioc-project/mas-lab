#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A Flavour's ``bind`` names infra rows; it is deployment, so it stays out of the run hash."""

from __future__ import annotations

from mas.lab.benchmark.cache import extract_flavour_info


def test_bind_does_not_change_the_flavour_cache_key() -> None:
    plain = {"observability": ["native"], "agent_comm": {"protocol": "agent-local"}}
    bound = {**plain, "bind": {"agent_comm": ["desk-node"]}}
    assert extract_flavour_info(bound) == extract_flavour_info(plain)
    assert extract_flavour_info({**plain, "observability": []}) != extract_flavour_info(plain)
