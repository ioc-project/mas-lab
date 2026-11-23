#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A conversation is keyed by (caller, contextId), never by the caller-chosen contextId alone."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from library_ioa.plugins.a2a.exposure import A2AExposure
from mas.ctl.session.exposure import make_runtime_handler, session_key
from test_exposure import _stop_instance

ALICE = "a" * 40
BOB = "b" * 40


def test_two_callers_with_the_same_context_id_get_two_conversations() -> None:
    # kills: keying by contextId alone (bob joins alice's conversation and reads her history)
    handler = make_runtime_handler(_stop_instance())
    first = handler("alice secret", session_id="shared", caller="alice")
    second = handler("bob hello", session_id="shared", caller="bob")
    assert first["context_id"] == second["context_id"] == "shared"  # the caller sees the id it chose
    alice = handler.manager.get(session_key("alice", "shared"))
    bob = handler.manager.get(session_key("bob", "shared"))
    assert alice is not bob and alice.instance is not bob.instance
    assert [u for u, _ in alice.instance.driver.ctx.turn_history] == ["alice secret"]
    assert [u for u, _ in bob.instance.driver.ctx.turn_history] == ["bob hello"]


def test_the_same_caller_and_context_id_continue_one_conversation() -> None:
    handler = make_runtime_handler(_stop_instance())
    handler("one", session_id="c", caller="alice")
    handler("two", session_id="c", caller="alice")
    session = handler.manager.get(session_key("alice", "c"))
    assert [u for u, _ in session.instance.driver.ctx.turn_history] == ["one", "two"]


def test_without_a_caller_the_key_is_the_context_id_as_before() -> None:
    # kills: changing the key of the default (unauthenticated) path
    assert session_key("", "alice") == "alice"
    handler = make_runtime_handler(_stop_instance())
    handler("x", session_id="alice")
    assert handler.manager.get("alice") is not None


def test_an_anonymous_context_id_cannot_forge_a_callers_key() -> None:
    # kills: a caller-less id shaped like another caller's key reaching that caller's conversation
    forged = session_key("alice", "shared")
    with pytest.raises(ValueError):
        session_key("", forged)
    handler = make_runtime_handler(_stop_instance())
    handler("secret", session_id="shared", caller="alice")
    with pytest.raises(ValueError):
        handler("steal", session_id=forged)


def test_over_http_two_admitted_callers_on_one_context_id_stay_apart() -> None:
    # kills: the listener passing no caller to the handler, so the fix never applies on the wire
    from secret_fakes import bound_secrets

    handler = make_runtime_handler(_stop_instance())
    endpoint = {
        "protocol": "a2a",
        "usage": "listen",
        "url": "http://127.0.0.1:8124",
        "a2a": {"admission": {"callers": {"alice": {"secret": "alice"}, "bob": {"secret": "bob"}}}},
    }
    with bound_secrets({"alice": ALICE, "bob": BOB}):
        app = A2AExposure(endpoint=endpoint).build_app(
            {"metadata": {"name": "solo"}, "spec": {"description": "l"}}, handler
        )

        def send(client: TestClient, token: str, text: str) -> dict:
            response = client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "role": "ROLE_USER",
                            "parts": [{"text": text}],
                            "messageId": f"m-{text}",
                            "contextId": "shared",
                        }
                    },
                },
                headers={"A2A-Version": "1.0", "Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200, response.text
            return response.json()

        with TestClient(app) as client:
            send(client, ALICE, "from alice")
            send(client, BOB, "from bob")
    alice = handler.manager.get(session_key("alice", "shared"))
    bob = handler.manager.get(session_key("bob", "shared"))
    assert [u for u, _ in alice.instance.driver.ctx.turn_history] == ["from alice"]
    assert [u for u, _ in bob.instance.driver.ctx.turn_history] == ["from bob"]
