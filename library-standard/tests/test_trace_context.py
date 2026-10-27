# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""trace_context mints a child span id on each send."""

from __future__ import annotations

import pytest
from mas.library.standard.lib.comm.headers import get_headers
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.agentcomm.trace_context import (
    TraceContextLayer,
    child_traceparent,
)
from mas.runtime.boundary.agentcomm.protocol import AgentCommError


def test_child_traceparent_keeps_trace_id() -> None:
    parent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    child = child_traceparent(parent)
    _ver, trace_id, span_id, flags = child.split("-")
    assert trace_id == "a" * 32
    assert span_id != "b" * 16
    assert flags == "01"
    assert child_traceparent(None).startswith("00-")


def test_invalid_w3c_parent_starts_a_new_root() -> None:
    zero_trace = "00-" + "0" * 32 + "-" + "b" * 16 + "-01"
    ff_version = "ff-" + "a" * 32 + "-" + "b" * 16 + "-01"
    zero_span = "00-" + "a" * 32 + "-" + "0" * 16 + "-01"
    for parent in (zero_trace, ff_version, zero_span, "not-a-traceparent"):
        child = child_traceparent(parent)
        version, trace_id, span_id, _flags = child.split("-")
        assert version == "00"
        assert trace_id != "0" * 32
        assert span_id != "0" * 16
        if parent is ff_version:
            assert not child.startswith("ff-")
        if parent is zero_trace:
            assert trace_id != "0" * 32


def test_mixed_case_traceparent_is_recognised() -> None:
    parent = "00-" + "c" * 32 + "-" + "d" * 16 + "-01"
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    TraceContextLayer(LocalAgentComm(run_turn)).send(
        "b", "t", headers={"Traceparent": parent}
    )
    child = seen[-1]["traceparent"]
    assert "Traceparent" not in seen[-1]
    assert child.split("-")[1] == "c" * 32
    assert child.split("-")[2] != "d" * 16


def test_unbound_trace_context_raises() -> None:
    with pytest.raises(AgentCommError, match="unbound"):
        TraceContextLayer().send("b", "t")


def test_asend_mints_child_span() -> None:
    import asyncio

    seen: list[dict[str, str]] = []

    class _Run:
        def __call__(self, *_args: object) -> str:
            raise AssertionError("sync send should not run")

        async def asend(self, *_args: object) -> str:
            seen.append(get_headers())
            return "ok"

    parent = "00-" + "e" * 32 + "-" + "f" * 16 + "-01"

    async def _go() -> str:
        return await TraceContextLayer(LocalAgentComm(_Run())).asend(
            "b", "t", headers={"traceparent": parent}
        )

    assert asyncio.run(_go()) == "ok"
    assert seen[-1]["traceparent"].split("-")[1] == "e" * 32


def test_trace_context_mints_child_span_on_send() -> None:
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    parent = "00-" + "c" * 32 + "-" + "d" * 16 + "-01"
    layer = TraceContextLayer(LocalAgentComm(run_turn))
    assert layer.send("b", "t", headers={"traceparent": parent, "k": "v"}) == "ok"
    child = seen[-1]["traceparent"]
    assert child.split("-")[1] == "c" * 32
    assert child.split("-")[2] != "d" * 16
    assert seen[-1]["k"] == "v"
    assert get_headers() == {}
