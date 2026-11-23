#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A2A admission: who may call a listener, and who the handler is told called."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from library_ioa.plugins.a2a.exposure import A2AExposure
from secret_fakes import bound_secrets

ALICE = "a" * 40
BOB = "b" * 40
MANIFEST = {"metadata": {"name": "solo"}, "spec": {"description": "listener"}}
CALLERS = {"alice": {"secret": "alice"}, "bob": {"secret": "bob"}}
SECRETS = {"alice": ALICE, "bob": BOB}


def _endpoint(admission: dict | None, **a2a) -> dict:
    block = dict(a2a)
    if admission is not None:
        block["admission"] = admission
    return {"protocol": "a2a", "usage": "listen", "url": "http://127.0.0.1:8123", "a2a": block}


def _rpc(client: TestClient, text: str, token: str | None, context_id: str = "ctx") -> object:
    headers = {"A2A-Version": "1.0"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
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
                    "contextId": context_id,
                }
            },
        },
        headers=headers,
    )


def _app(admission: dict | None, seen: list):
    def handler(prompt, **kwargs):
        seen.append(kwargs.get("caller"))
        return {"text": "ok", "context_id": kwargs.get("session_id")}

    exposure = A2AExposure(endpoint=_endpoint(admission))
    return exposure.build_app(MANIFEST, handler)


def test_without_admission_everyone_is_admitted_and_no_caller_is_named() -> None:
    # kills: admission switched on by default, which would break every experiment
    seen: list = []
    with TestClient(_app(None, seen)) as client:
        assert _rpc(client, "hi", None).status_code == 200
    assert seen == [None]


def test_with_admission_a_missing_or_wrong_token_is_refused_and_the_card_stays_public() -> None:
    # kills: admission that lets a request without a token through; a card behind the wall
    seen: list = []
    with bound_secrets(SECRETS):
        app = _app({"callers": {"alice": {"secret": "alice"}}}, seen)
        with TestClient(app) as client:
            for token in (None, "wrong", ALICE[:-1], ALICE + "x", ""):
                response = _rpc(client, "hi", token)
                assert response.status_code == 401, token
                assert response.json() == {"error": "unauthorized"}
            assert client.get("/.well-known/agent-card.json").status_code == 200
    assert seen == []


def test_the_caller_is_the_name_in_the_table_never_what_the_request_claims() -> None:
    # kills: reading the caller from a header or a body field the request supplies
    seen: list = []
    with bound_secrets(SECRETS):
        with TestClient(_app({"callers": CALLERS}, seen)) as client:
            headers = {"A2A-Version": "1.0", "Authorization": f"Bearer {BOB}", "X-Caller": "alice"}
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {"role": "ROLE_USER", "parts": [{"text": "x"}], "messageId": "m", "contextId": "c"},
                    "metadata": {"caller": "alice"},
                },
            }
            assert client.post("/", json=body, headers=headers).status_code == 200
            assert _rpc(client, "again", ALICE).status_code == 200
    assert seen == ["bob", "alice"]


@pytest.mark.parametrize(
    "admission",
    [
        {},
        {"callers": {}},
        {"callers": None},
        {"callers": {"alice": ""}},
        {"callers": {"alice": "short"}},
        {"callers": {"": {"secret": "alice"}}},
        {"callers": {"a\nb": {"secret": "alice"}}},
    ],
)
def test_an_empty_or_weak_policy_is_refused_not_read_as_open(admission) -> None:
    # kills: an empty policy that admits everyone (or nobody) silently; a literal token
    with bound_secrets(SECRETS):
        with pytest.raises(ValueError):
            A2AExposure(endpoint=_endpoint(admission))


@pytest.mark.parametrize("secrets", [{"alice": "short"}, {"alice": ALICE, "bob": ALICE}, {"bob": BOB}])
def test_a_short_shared_or_missing_token_admits_no_one(secrets) -> None:
    # kills: a weak token, an ambiguous caller identity, or a missing secret read as admit
    seen: list = []
    with bound_secrets(secrets):
        with TestClient(_app({"callers": CALLERS}, seen)) as client:
            assert _rpc(client, "hi", secrets.get("alice", ALICE)).status_code == 401
    assert seen == []


def test_a_rotated_token_is_used_without_a_restart() -> None:
    # kills: tokens revealed once at build, so a rotation keeps the old token valid
    seen: list = []
    with bound_secrets(SECRETS) as resolver:
        with TestClient(_app({"callers": CALLERS}, seen)) as client:
            resolver.store.put("alice", "c" * 40)
            assert _rpc(client, "new", "c" * 40).status_code == 200
            assert _rpc(client, "old", ALICE).status_code == 401
    assert seen == ["alice"]


def test_admission_with_a_grpc_listener_is_refused() -> None:
    # kills: an unauthenticated gRPC port next to an authenticated HTTP one
    with bound_secrets(SECRETS):
        with pytest.raises(ValueError, match="gRPC|grpc"):
            A2AExposure(endpoint=_endpoint({"callers": {"alice": {"secret": "alice"}}}, grpc_port=50051))


def test_a_callers_bearer_is_not_in_the_debug_log_of_the_served_request(caplog: pytest.LogCaptureFixture) -> None:
    # kills: the SDK call context holding every request header, logged by a2a-sdk at debug level
    import logging

    seen: list = []
    with bound_secrets(SECRETS), caplog.at_level(logging.DEBUG):
        for name in ("a2a", "a2a.server.agent_execution.active_task"):
            logging.getLogger(name).setLevel(logging.DEBUG)
        with TestClient(_app({"callers": CALLERS}, seen)) as client:
            assert _rpc(client, "hi", ALICE).status_code == 200
            assert client.post(
                "/a2a/rest/message:send",
                json={"message": {"role": "ROLE_USER", "parts": [{"text": "r"}], "messageId": "m-r"}},
                headers={"A2A-Version": "1.0", "Authorization": f"Bearer {BOB}"},
            ).status_code in (200, 404)
    assert seen and seen[0] == "alice"
    assert caplog.records, "the a2a debug log was not captured"
    assert ALICE not in caplog.text and BOB not in caplog.text
