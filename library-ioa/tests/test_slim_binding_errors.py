#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A binding error that echoes the shared secret never leaves the SLIM layer: not in the text, not on the chain."""

from __future__ import annotations

import asyncio
import logging
import traceback

import pytest
import slim_node_fake as slim_fake
from library_ioa.plugins.slim.actor import ConnectionActor, SlimBindingError
from mas.runtime.boundary.agentcomm.protocol import AgentCommError
from test_slim_comm import _SECRET, _patch_slim_bindings  # noqa: F401  (autouse fixture patches the bindings)

FakeWorld = slim_fake.FakeWorld
ERRORS = [RuntimeError, TypeError]


def _assert_clean(error: BaseException, message: str) -> None:
    assert isinstance(error, AgentCommError)
    assert str(error) == message
    assert error.__cause__ is None and error.__context__ is None
    rendered = "".join(traceback.format_exception(error)) + repr(error) + repr(error.args)
    assert _SECRET not in rendered


async def _echo(kind: type[Exception]) -> None:
    raise kind(f"bad argument {_SECRET}")


@pytest.fixture
def actor():
    FakeWorld.reset()
    link = ConnectionActor(endpoint="http://127.0.0.1:46357", timeout_s=0.5)
    link.start()
    yield link
    link.stop()


_ENTRIES = {
    "submit": lambda actor, coro: actor.submit(coro),
    "asubmit": lambda actor, coro: asyncio.run(actor.asubmit(coro, timeout=1.0)),
    "guard": lambda actor, coro: asyncio.run(actor._guard(coro)),
}


@pytest.mark.parametrize("entry", sorted(_ENTRIES))
@pytest.mark.parametrize("kind", ERRORS)
def test_a_binding_error_is_reported_by_kind_only(actor, entry, kind, caplog) -> None:
    # kills: quoting the binding's text, chaining it, or letting TypeError/AttributeError through raw
    with caplog.at_level(logging.DEBUG), pytest.raises(AgentCommError) as raised:
        _ENTRIES[entry](actor, _echo(kind))
    _assert_clean(raised.value, "slim binding error")
    assert _SECRET not in caplog.text


@pytest.mark.parametrize("kind", ERRORS)
def test_start_reports_a_binding_error_by_kind_only(monkeypatch, kind) -> None:
    class _Echoing:
        SlimError = slim_fake.SlimError
        uniffi_set_event_loop = staticmethod(slim_fake.uniffi_set_event_loop)
        is_initialized = staticmethod(lambda: True)

        @staticmethod
        def new_insecure_client_config(endpoint):
            raise kind(f"config {endpoint} {_SECRET}")

    monkeypatch.setattr("library_ioa.plugins.slim.actor.load_slim_bindings", lambda: _Echoing)
    link = ConnectionActor(endpoint="http://127.0.0.1:46357", timeout_s=0.5)
    with pytest.raises(AgentCommError) as raised:
        link.start()
    _assert_clean(raised.value, "slim link start failed")


def test_an_idle_read_keeps_its_kind_without_the_binding_text(actor) -> None:
    async def idle() -> None:
        raise slim_fake.SessionError(f"receive timeout {_SECRET}")

    with pytest.raises(SlimBindingError) as raised:
        actor.submit(idle())
    assert raised.value.kind == "idle_timeout"
    _assert_clean(raised.value, "slim read timed out")
