#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Inbound ``mas.headers`` metadata: honoured only when a layer above carries its headers that way."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role
from library_ioa.plugins.a2a.layer import HEADERS_KEY, A2ALayer
from mas.library.standard.lib.stack import Envelope, Layer


class _CarriesHeaders(Layer):
    """Sends its headers as metadata and verifies what comes in (it overrides ``serve``)."""

    HEADERS_IN_METADATA = True

    def serve(self, handler):  # type: ignore[no-untyped-def]
        return handler


class _CarriesHeadersUnverified(Layer):
    HEADERS_IN_METADATA = True


class _Plain(Layer):
    pass


def _context(**metadata: Any) -> SimpleNamespace:
    message = new_text_message("hi", role=Role.ROLE_USER)
    message.metadata.update(metadata)
    return SimpleNamespace(message=message, context_id="c1", task_id="t1", current_task=None)


async def _headers_seen(above: list[Layer], **metadata: Any) -> dict[str, str]:
    layer = A2ALayer()
    layer.bind_above(above)
    seen: list[Envelope] = []

    async def handler(envelope: Envelope) -> str:
        seen.append(envelope)
        return "ok"

    layer.serve(handler)
    await layer._turn("hi", _context(**metadata))
    return dict(seen[0].headers)


async def test_a_peer_cannot_inject_headers_when_no_layer_above_reads_them() -> None:
    """Kills: honouring ``mas.headers`` unconditionally (an unauthenticated peer would set any envelope header)."""
    injected = {HEADERS_KEY: {"authorization": "Bearer attacker", "x-trace": "1"}}
    assert "authorization" not in await _headers_seen([], **injected)
    assert "authorization" not in await _headers_seen([_Plain()], **injected)


async def test_headers_are_honoured_when_a_layer_above_carries_its_own_that_way() -> None:
    injected = {HEADERS_KEY: {"authorization": "Bearer ok"}}
    assert (await _headers_seen([_CarriesHeaders()], **injected))["authorization"] == "Bearer ok"


async def test_headers_are_not_read_by_a_layer_that_does_not_verify() -> None:
    injected = {HEADERS_KEY: {"authorization": "Bearer attacker"}}
    assert "authorization" not in await _headers_seen([_CarriesHeadersUnverified()], **injected)


async def test_a_peer_never_overrides_the_trace_context() -> None:
    carried = {HEADERS_KEY: {"traceparent": "00-evil-evil-01", "Tracestate": "x=1", "authorization": "Bearer ok"}}
    seen = await _headers_seen([_CarriesHeaders()], traceparent="00-aa-bb-01", **carried)
    assert seen["traceparent"] == "00-aa-bb-01"
    assert "tracestate" not in {k.lower() for k in seen}
    assert seen["authorization"] == "Bearer ok"


async def _meta_seen(above: list[Layer]) -> dict[str, object]:
    layer = A2ALayer()
    layer.bind_above(above)
    seen: list[Envelope] = []

    async def handler(envelope: Envelope) -> str:
        seen.append(envelope)
        return "ok"

    layer.serve(handler)
    await layer._turn("hi", _context())
    return dict(seen[0].meta)


async def test_the_inbound_envelope_has_the_extra_fields_only_when_a_layer_above_verifies() -> None:
    """With no layer above that wraps inbound, the handler gets the envelope it always got."""
    extra = {"protocol", "message_id", "task_id", "metadata"}
    assert not extra & set(await _meta_seen([]))
    assert not extra & set(await _meta_seen([_Plain()]))
    assert extra <= set(await _meta_seen([_CarriesHeaders()]))


async def test_the_forwarded_trace_headers_do_not_depend_on_the_layers_above() -> None:
    assert (await _headers_seen([], traceparent="00-aa-bb-01"))["traceparent"] == "00-aa-bb-01"


async def test_the_task_id_is_set_only_for_a_follow_up_on_an_open_task() -> None:
    layer = A2ALayer()
    layer.bind_above([_CarriesHeaders()])
    seen: list[Envelope] = []

    async def handler(envelope: Envelope) -> str:
        seen.append(envelope)
        return "ok"

    layer.serve(handler)
    first = _context()
    await layer._turn("hi", first)
    follow_up = _context()
    follow_up.current_task = SimpleNamespace(id="t-open")
    await layer._turn("hi", follow_up)
    assert [e.meta["task_id"] for e in seen] == ["", "t-open"]


async def test_a_refusal_is_answered_unauthenticated_for_a_streaming_send_and_forgotten() -> None:
    """Kills: a refusal noted for a streaming send that is never popped (a leak, and the caller sees no 401)."""
    import pytest
    from library_ioa.plugins.a2a.layer import _RPC_TOKEN, _gated_handler, _Refusals
    from mas.library.standard.lib.stack import PeerChallenge

    sb = pytest.importorskip("slim_bindings")
    refused = _Refusals()
    kept_under: list[str] = []

    class Base:
        def __init__(self, card, request_handler) -> None:
            pass

        async def SendStreamingMessage(self, request, context):  # noqa: N802
            if request.message.message_id == "m1":  # the turn refusing, under the key of the RPC being served
                kept_under.append(_RPC_TOKEN.get() or "")
                refused.add(kept_under[-1], PeerChallenge({}, code="x"))
            yield "event"

    handler = _gated_handler(Base, None, None, refused)  # type: ignore[arg-type]
    request = SimpleNamespace(message=SimpleNamespace(message_id="m1"))
    got: list[str] = []
    with pytest.raises(sb.RpcError.Rpc):
        async for item in handler.SendStreamingMessage(request, None):
            got.append(item)
    assert got == ["event"]
    assert kept_under[0] and refused.pop(kept_under[0]) is None  # popped by the RPC that was refused: nothing is left
    assert [
        i async for i in handler.SendStreamingMessage(SimpleNamespace(message=SimpleNamespace(message_id="m2")), None)
    ] == ["event"]
    # a refusal kept under a message id a caller chose is not handed to another RPC that sends the same id
    refused.add("m3", PeerChallenge({}, code="x"))
    assert [
        i async for i in handler.SendStreamingMessage(SimpleNamespace(message=SimpleNamespace(message_id="m3")), None)
    ] == ["event"]


async def test_the_credential_carriers_are_taken_out_of_the_message_the_sdk_keeps() -> None:
    """Kills: `mas.headers` and the keys a verifier names as its credential staying in the message the SDK stores.

    The handler stands for the identity layer's verifier: it names `x/cred` as the credential carrier through the
    callback the transport offers. Without the transport's strip the kept message still holds both.
    """
    layer = A2ALayer()
    layer.bind_above([_CarriesHeaders()])
    context = _context(**{HEADERS_KEY: {"authorization": "Bearer HDR-VALUE"}, "x/cred": "CRED-VALUE", "x/keep": "kept"})

    async def handler(envelope: Envelope) -> str:
        envelope.meta["forget_metadata"](["x/cred"])
        return "ok"

    layer.serve(handler)
    await layer._turn("hi", context)
    kept = dict(context.message.metadata)
    assert "CRED-VALUE" not in str(kept) and "HDR-VALUE" not in str(kept) and kept == {"x/keep": "kept"}
