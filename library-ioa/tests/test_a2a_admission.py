#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A2A admission: who may call a listener, and who the handler is told called."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from library_ioa.plugins.a2a.exposure import A2AExposure

ALICE = "a" * 40
BOB = "b" * 40
MANIFEST = {"metadata": {"name": "solo"}, "spec": {"description": "listener"}}


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
    app = _app({"callers": {"alice": ALICE}}, seen)
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
    with TestClient(_app({"callers": {"alice": ALICE, "bob": BOB}}, seen)) as client:
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
        {"callers": {"": ALICE}},
        {"callers": {"a\nb": ALICE}},
    ],
)
def test_an_empty_or_weak_policy_is_refused_not_read_as_open(admission) -> None:
    # kills: an empty policy that admits everyone (or nobody) silently; a short or unset token (unset env: is "")
    with pytest.raises(ValueError):
        A2AExposure(endpoint=_endpoint(admission))


def test_admission_with_a_grpc_listener_is_refused() -> None:
    # kills: an unauthenticated gRPC port next to an authenticated HTTP one
    with pytest.raises(ValueError, match="gRPC|grpc"):
        A2AExposure(endpoint=_endpoint({"callers": {"alice": ALICE}}, grpc_port=50051))


def test_two_callers_sharing_one_token_are_refused() -> None:
    # kills: an ambiguous caller identity (and session key) when two callers hold the same token
    with pytest.raises(ValueError, match="shares a token"):
        A2AExposure(endpoint=_endpoint({"callers": {"alice": ALICE, "bob": ALICE}}))
