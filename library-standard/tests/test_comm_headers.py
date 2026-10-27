# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Header validation, hop allowlist, and local send ambient headers."""

from __future__ import annotations

import logging

import pytest
from mas.library.standard.lib.comm.headers import (
    MAX_HEADER_COUNT,
    MAX_HEADER_TOTAL,
    MAX_HEADER_VALUE,
    bound_headers,
    get_headers,
    hop_headers,
    validate_headers,
)
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.runtime.boundary.agentcomm.protocol import AgentCommError


def test_validate_headers_rejects_newline_and_non_token() -> None:
    with pytest.raises(AgentCommError, match="control"):
        validate_headers({"k": "a\nb"})
    with pytest.raises(AgentCommError, match="control"):
        validate_headers({"k": "a\x00b"})
    with pytest.raises(AgentCommError, match="control"):
        validate_headers({"k": "a\x7fb"})
    with pytest.raises(AgentCommError, match="control"):
        validate_headers({"k": "a\u2028b"})
    with pytest.raises(AgentCommError, match="token"):
        validate_headers({"bad name": "v"})
    with pytest.raises(AgentCommError, match="token"):
        validate_headers({"o/evil": "v"})
    assert validate_headers({"traceparent": "00-aa-bb-01"})["traceparent"] == "00-aa-bb-01"


def test_bound_headers_always_validates() -> None:
    with pytest.raises(AgentCommError, match="token"):
        with bound_headers({"Bad Name\n": "x"}):
            pass


def test_header_names_are_case_insensitive() -> None:
    assert validate_headers({"Traceparent": "00-aa-bb-01", "K": "v"}) == {
        "traceparent": "00-aa-bb-01",
        "k": "v",
    }
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    LocalAgentComm(run_turn).send("b", "t", headers={"Authorization": "secret"})
    assert seen[-1] == {"authorization": "secret"}
    assert "Authorization" not in seen[-1]
    assert hop_headers({"Traceparent": "00-aa-bb-01", "Authorization": "secret"}) == {
        "traceparent": "00-aa-bb-01",
    }


def test_validate_headers_caps_count_and_total() -> None:
    too_many = {f"h{i}": "v" for i in range(MAX_HEADER_COUNT + 1)}
    with pytest.raises(AgentCommError, match="too many"):
        validate_headers(too_many)
    chunk = "x" * MAX_HEADER_VALUE
    bulky = {f"h{i}": chunk for i in range((MAX_HEADER_TOTAL // MAX_HEADER_VALUE) + 1)}
    with pytest.raises(AgentCommError, match="total size"):
        validate_headers(bulky)


def test_authorization_and_mas_headers_do_not_hop() -> None:
    leaked: list[dict[str, str]] = []

    def run_b(*_args: object) -> str:
        leaked.append(get_headers())
        return "ok"

    def run_a(*_args: object) -> str:
        LocalAgentComm(run_b).send("c", "next")
        return "ok"

    LocalAgentComm(run_a).send(
        "b",
        "first",
        headers={"authorization": "secret", "mas.intent": "task", "traceparent": "00-aa-bb-01"},
    )
    assert leaked[-1].get("authorization") is None
    assert leaked[-1].get("mas.intent") is None
    assert leaked[-1]["traceparent"] == "00-aa-bb-01"


def test_explicit_headers_still_win_on_this_hop() -> None:
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    comm = LocalAgentComm(run_turn)
    with bound_headers({"authorization": "from-ambient"}):
        comm.send("b", "t", headers={"k": "v", "authorization": "explicit"})
    assert seen[-1]["k"] == "v"
    assert seen[-1]["authorization"] == "explicit"
    assert get_headers() == {}


def test_hop_headers_allowlist() -> None:
    assert hop_headers(
        {
            "traceparent": "00-aa-bb-01",
            "tracestate": "vendor=1",
            "baggage": "user=1",
            "authorization": "secret",
            "mas.intent": "task",
        }
    ) == {
        "traceparent": "00-aa-bb-01",
        "tracestate": "vendor=1",
        "baggage": "user=1",
    }


def test_local_agent_comm_sets_headers_for_the_turn() -> None:
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    comm = LocalAgentComm(run_turn)
    assert comm.send("b", "t", headers={"k": "v"}) == "ok"
    assert seen[-1] == {"k": "v"}
    assert get_headers() == {}


def test_ambient_headers_reset_after_handler_error() -> None:
    def run_turn(*_args: object) -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        LocalAgentComm(run_turn).send("b", "t", headers={"k": "v"})
    assert get_headers() == {}


def test_asend_sets_headers_for_the_turn() -> None:
    import asyncio

    seen: list[dict[str, str]] = []

    class _Run:
        def __call__(self, *_args: object) -> str:
            raise AssertionError("sync send should not run")

        async def asend(self, *_args: object) -> str:
            seen.append(get_headers())
            return "ok"

    async def _go() -> str:
        return await LocalAgentComm(_Run()).asend("b", "t", headers={"k": "v"})

    assert asyncio.run(_go()) == "ok"
    assert seen[-1] == {"k": "v"}
    assert get_headers() == {}


def test_w3c_tracestate_does_not_fail_a_send(
    caplog: pytest.LogCaptureFixture,
) -> None:
    legal = ",".join(f"v{i}=s{i}" for i in range(17))
    over = ",".join(f"v{i}=s{i}" for i in range(33))
    seen: list[dict[str, str]] = []

    def run_turn(*_args: object) -> str:
        seen.append(get_headers())
        return "ok"

    comm = LocalAgentComm(run_turn)
    assert comm.send("b", "t", headers={"tracestate": legal}) == "ok"
    assert seen[-1]["tracestate"] == legal
    with caplog.at_level(logging.DEBUG, logger="mas.library.standard.lib.comm.headers"):
        assert comm.send("b", "t", headers={"tracestate": over, "k": "v"}) == "ok"
    assert "tracestate" not in seen[-1]
    assert seen[-1]["k"] == "v"
    assert "dropping tracestate" in caplog.text
    assert validate_headers({"tracestate": over, "k": "v"}) == {"k": "v"}
    assert "baggage" not in validate_headers(
        {"baggage": ",".join(f"k{i}=v{i}" for i in range(65))}
    )
