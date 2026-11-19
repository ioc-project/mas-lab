#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A2A typed hooks: metadata shapes must not fail a send."""

from __future__ import annotations

import json
from types import SimpleNamespace

from library_ioa.plugins.a2a.hooks import (
    OBSERVE_KEY,
    inject_observe_metadata,
    observe_metadata,
    read_observe_metadata,
)
from mas.runtime.transport.config import ProtocolTransportConfig, TransportObservabilityConfig
from mas.runtime.transport.emit import bind_config, reset


def test_read_observe_metadata_from_dict_and_struct() -> None:
    payload = {"session_id": "s1", "last_agent_name": "caller"}
    encoded = {OBSERVE_KEY: json.dumps(payload)}
    assert read_observe_metadata(encoded)["last_agent_name"] == "caller"
    assert read_observe_metadata(None) == {}
    assert read_observe_metadata(object()) == {}

    from google.protobuf.struct_pb2 import Struct

    struct = Struct()
    struct.update(encoded)
    assert read_observe_metadata(struct)["last_agent_name"] == "caller"


def test_inject_observe_metadata_on_none_and_mapping() -> None:
    reset()
    bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig(propagate_metadata=True)))
    extra = observe_metadata(session_id="s", agent_id="caller")
    assert extra

    message = SimpleNamespace(metadata=None)
    inject_observe_metadata(message, extra)
    assert message.metadata[OBSERVE_KEY]

    other = SimpleNamespace(metadata={"keep": "1"})
    inject_observe_metadata(other, extra)
    assert other.metadata["keep"] == "1"
    assert OBSERVE_KEY in other.metadata
    reset()


def test_propagate_metadata_off_injects_nothing() -> None:
    """Mutation: observe_metadata still returns the observe key when propagate_metadata is false."""
    reset()
    bind_config(
        TransportObservabilityConfig(
            present=True,
            defaults=ProtocolTransportConfig(propagate_metadata=False),
        )
    )
    assert observe_metadata(session_id="s", agent_id="caller") == {}
    reset()


def test_default_config_injects_no_metadata() -> None:
    """Mutation: ProtocolTransportConfig.propagate_metadata defaults to True again."""
    reset()
    bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    assert observe_metadata(session_id="s", agent_id="caller") == {}
    reset()


def test_nothing_to_say_injects_no_key() -> None:
    """Mutation: observe_metadata returns {'observe': '{}'} for an empty payload."""
    reset()
    bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig(propagate_metadata=True)))
    assert observe_metadata() == {}
    reset()


def test_hostile_metadata_is_bounded_and_typed() -> None:
    """Mutation: read_observe_metadata copies the peer's JSON through unchecked."""
    big = "x" * 100_000
    assert read_observe_metadata({OBSERVE_KEY: json.dumps({"session_id": big})}) == {}
    raw = json.dumps(
        {
            "session_id": {"nested": 1},
            "last_agent_name": "n" * 1000,
            "agent_sequence": True,
            "traceparent": 5,
            "extra": "dropped",
        }
    )
    out = read_observe_metadata({OBSERVE_KEY: raw})
    assert set(out) == {"last_agent_name"}
    assert len(out["last_agent_name"]) == 256
    assert read_observe_metadata({OBSERVE_KEY: "[" * 100_000}) == {}


def test_peer_label_drops_credentials_path_and_query() -> None:
    from library_ioa.plugins.a2a.hooks import peer_label

    assert peer_label("https://user:secret@peer.example:8443/a/b?token=1") == "https://peer.example:8443"
    assert peer_label("not a url") == ""


def _capture():
    from mas.runtime.schema.transport import TransportEvent
    from mas.runtime.transport import emit

    events: list[TransportEvent] = []

    class Sink:
        def emit(self, event: TransportEvent) -> None:
            events.append(event)

    emit.reset()
    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    emit.bind_sink(Sink())
    return events


def _before(interceptor, context, *, method: str = "send_message_streaming"):
    from a2a.client.interceptors import BeforeArgs
    from a2a.types.a2a_pb2 import Message, SendMessageRequest

    request = SendMessageRequest(message=Message(message_id="m1", context_id="c1"))
    return BeforeArgs(input=request, method=method, agent_card=None, context=context)


def _after(context, result, *, method: str = "send_message_streaming"):
    from a2a.client.interceptors import AfterArgs

    return AfterArgs(result=result, method=method, agent_card=None, context=context)


async def test_streaming_call_is_one_sent_and_one_completed_with_the_same_key() -> None:
    """Mutation: after() emits completed for every chunk, or with a key built from nothing."""
    from a2a.client.client import ClientCallContext
    from a2a.types.a2a_pb2 import Message, StreamResponse, Task, TaskState, TaskStatus
    from library_ioa.plugins.a2a.hooks import A2ATransportInterceptor, start_call_state

    events = _capture()
    try:
        interceptor = A2ATransportInterceptor()
        context = ClientCallContext()
        start_call_state(context.state, key="", source="caller", target="peer")
        await interceptor.before(_before(interceptor, context))
        working = StreamResponse(task=Task(id="t1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)))
        done = StreamResponse(task=Task(id="t1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)))
        for chunk in (working, working):
            await interceptor.after(_after(context, chunk))
        assert [e.kind for e in events] == ["transport.message.sent"]  # a working task is not a finished call
        for chunk in (done, done):
            await interceptor.after(_after(context, chunk))
        del Message
    finally:
        from mas.runtime.transport import emit

        emit.reset()
    kinds = [e.kind for e in events]
    assert kinds == ["transport.message.sent", "transport.message.completed"]
    assert events[0].correlation_key == events[1].correlation_key == "c1||m1"
    assert events[0].attributes["source"] == "caller"
    assert events[1].attributes["target"] == "peer"


async def test_failed_call_emits_an_error_with_the_sent_key() -> None:
    """Mutation: the failure path emits nothing, so the sent event dangles."""
    from a2a.client.client import ClientCallContext
    from library_ioa.plugins.a2a.hooks import A2ATransportInterceptor, finish_call, start_call_state

    events = _capture()
    try:
        interceptor = A2ATransportInterceptor()
        context = ClientCallContext()
        start_call_state(context.state, key="", source="caller", target="peer")
        await interceptor.before(_before(interceptor, context))
        finish_call(context.state, error=TimeoutError("slow"))
        finish_call(context.state, error=TimeoutError("slow"))  # once
    finally:
        from mas.runtime.transport import emit

        emit.reset()
    assert [e.kind for e in events] == ["transport.message.sent", "transport.error"]
    assert events[1].correlation_key == events[0].correlation_key
    assert events[1].attributes["error_type"] == "TimeoutError"


async def test_concurrent_calls_do_not_share_state() -> None:
    """Mutation: the interceptor stores the key on itself, so two calls overwrite each other."""
    from a2a.client.client import ClientCallContext
    from library_ioa.plugins.a2a.hooks import A2ATransportInterceptor, start_call_state

    events = _capture()
    try:
        interceptor = A2ATransportInterceptor()
        first, second = ClientCallContext(), ClientCallContext()
        for context, who in ((first, "a"), (second, "b")):
            start_call_state(context.state, key="", source=who, target="peer")
            await interceptor.before(_before(interceptor, context, method="send_message"))
        from a2a.types.a2a_pb2 import Message, SendMessageResponse

        await interceptor.after(
            _after(first, SendMessageResponse(message=Message(message_id="r")), method="send_message")
        )
    finally:
        from mas.runtime.transport import emit

        emit.reset()
    completed = [e for e in events if e.kind == "transport.message.completed"]
    assert len(completed) == 1 and completed[0].attributes["source"] == "a"


async def test_send_binds_the_links_identity_not_the_callers() -> None:
    """Mutation: the sender is read from SendContext.sender, which a caller can set to anything."""
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from library_ioa.plugins.a2a.hooks import current_identity
    from mas.runtime.boundary.agentcomm.protocol import SendContext

    seen: list[tuple[str, str, str]] = []

    class Client:
        async def asend_message(self, *args, **kwargs):
            seen.append(current_identity())
            return {"status": "completed", "result": "ok"}

    plugin = A2AAgentComm.__new__(A2AAgentComm)
    plugin._client = Client()
    plugin.agent_id = "planner"
    await plugin.asend("worker", "task", context=SendContext(sender="evil"))
    assert seen == [("planner", "worker", "")]
    assert current_identity() == ("", "", "")  # unbound again after the call


def test_hooks_off_and_a_raising_sink_do_not_fail_the_call() -> None:
    from library_ioa.plugins.a2a.hooks import emit_sent
    from mas.runtime.transport import emit

    reset()
    emit_sent(key="k", source="a", target="b")  # hooks off: nothing built
    captured: list = []

    class Boom:
        def emit(self, event) -> None:
            captured.append(event)
            raise RuntimeError("sink")

    emit.bind_config(TransportObservabilityConfig(present=True, defaults=ProtocolTransportConfig()))
    emit.bind_sink(Boom())
    emit_sent(key="k", source="a", target="b")  # sink raised; the hook must swallow it
    assert captured
    emit.reset()


def test_peer_label_and_metadata_helpers_tolerate_junk() -> None:
    from library_ioa.plugins.a2a.hooks import inject_observe_metadata, peer_label, read_observe_metadata

    assert peer_label("") == ""
    assert peer_label("http://[") == ""

    class Items:
        def items(self):
            raise RuntimeError("no")

    assert read_observe_metadata(Items()) == {}

    class Proto:
        DESCRIPTOR = object()

    assert read_observe_metadata(Proto()) == {}
    assert read_observe_metadata({OBSERVE_KEY: 5}) == {}
    assert read_observe_metadata({OBSERVE_KEY: json.dumps({"agent_sequence": 3})})["agent_sequence"] == 3

    class Updating:
        def update(self, extra):
            self.extra = extra

    message = SimpleNamespace(metadata=Updating())
    inject_observe_metadata(message, {OBSERVE_KEY: "{}"})
    assert message.metadata.extra[OBSERVE_KEY] == "{}"

    class Broken:
        def update(self, extra):
            raise RuntimeError("no")

    inject_observe_metadata(SimpleNamespace(metadata=Broken()), {OBSERVE_KEY: "{}"})
    inject_observe_metadata(None, {OBSERVE_KEY: "{}"})
    inject_observe_metadata(SimpleNamespace(metadata={}), {})


def test_call_state_survives_unwritable_state_and_empty_key() -> None:
    from library_ioa.plugins.a2a.hooks import finish_call, start_call_state

    class Frozen:
        def __setitem__(self, key, value):
            raise RuntimeError("frozen")

    start_call_state(Frozen(), key="k", source="a", target="b")
    finish_call(None)
    finish_call({"mas.transport": {"key": "", "done": False}})
    finish_call({"mas.transport": {"key": "k", "done": False, "source": "a", "target": "b", "session_id": ""}})

    class Boom:
        def get(self, key):
            raise RuntimeError("no")

    finish_call(Boom())


async def test_interceptor_skips_other_methods_and_swallows_faults() -> None:
    from a2a.client.client import ClientCallContext
    from library_ioa.plugins.a2a.hooks import A2ATransportInterceptor

    events = _capture()
    try:
        interceptor = A2ATransportInterceptor()
        context = ClientCallContext()
        await interceptor.before(_before(interceptor, context, method="get_task"))
        await interceptor.after(_after(context, object(), method="get_task"))
        await interceptor.before(SimpleNamespace())
        await interceptor.after(SimpleNamespace())
        empty = SimpleNamespace(
            method="send_message",
            context=None,
            input=SimpleNamespace(message={"context_id": "", "taskId": "", "messageId": ""}),
        )
        await interceptor.before(empty)
        from a2a.types.a2a_pb2 import Message, StreamResponse, TaskState, TaskStatus, TaskStatusUpdateEvent

        context = ClientCallContext()
        await interceptor.after(
            _after(
                context,
                StreamResponse(
                    status_update=TaskStatusUpdateEvent(status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
                ),
            )
        )
        await interceptor.after(_after(context, StreamResponse(message=Message(message_id="r"))))
        await interceptor.after(_after(context, object()))
    finally:
        from mas.runtime.transport import emit

        emit.reset()
    assert [e.kind for e in events] == ["transport.message.sent"]


async def test_server_completed_and_error_hooks_use_the_same_key() -> None:
    from library_ioa.plugins.a2a.server.app import _A2ARequestHandler
    from mas.runtime.transport import emit

    events = _capture()
    try:
        handler = _A2ARequestHandler.__new__(_A2ARequestHandler)
        handler._agent_card = SimpleNamespace(name="worker")
        params = SimpleNamespace(
            message=SimpleNamespace(context_id="c", task_id="", message_id="m", metadata=None),
        )
        handler._emit_completed(params)
        handler._emit_error(params, TimeoutError("slow"))

        class BoomCard:
            @property
            def name(self) -> str:
                raise RuntimeError("no card")

        handler._agent_card = BoomCard()
        handler._emit_received(params)
    finally:
        emit.reset()
    assert [e.kind for e in events] == ["transport.message.completed", "transport.error"]
    assert events[0].correlation_key == events[1].correlation_key == "c||m"


async def test_a_peers_session_id_is_a_claim_not_this_runs_session() -> None:
    """Mutation: the received event takes its session_id from the peer's metadata."""
    from types import SimpleNamespace

    from library_ioa.plugins.a2a.server.app import _A2ARequestHandler
    from mas.runtime.transport import emit

    events = _capture()
    try:
        handler = _A2ARequestHandler.__new__(_A2ARequestHandler)
        handler._agent_card = SimpleNamespace(name="worker")
        message = SimpleNamespace(
            context_id="c",
            task_id="",
            message_id="m",
            metadata={OBSERVE_KEY: json.dumps({"session_id": "someone-elses", "last_agent_name": "caller"})},
        )
        handler._emit_received(SimpleNamespace(message=message))
    finally:
        emit.reset()
    (event,) = events
    assert event.session_id == ""
    assert event.attributes["peer_session_id"] == "someone-elses"
    assert event.attributes["source"] == "caller"
