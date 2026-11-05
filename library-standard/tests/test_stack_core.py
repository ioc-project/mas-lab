# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Generic stack: envelope, layer lifecycle, capability merge, trace stamp."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from mas.library.standard.lib.stack import Envelope, Layer, Stack, StackError
from mas.library.standard.testing.layer_conformance import RecordingLower, check_layer


class _Tag(Layer):
    """Marks the payload so order is visible."""

    def __init__(self, tag: str = "t", events: list[str] | None = None, **params):
        super().__init__(**params)
        self.tag = tag
        self.events: list[str] = events if events is not None else []

    def start(self):
        if not self._started:
            self.events.append(f"start:{self.tag}")
        super().start()

    def stop(self):
        if not self._stopped:
            self.events.append(f"stop:{self.tag}")
        super().stop()

    def send(self, envelope):
        return super().send(envelope.with_(payload=f"{envelope.payload}>{self.tag}"))


def test_a_layer_that_fails_to_stop_does_not_keep_the_layers_below_it_running():
    log: list[str] = []

    class _Fails(_Tag):
        def stop(self):
            super().stop()
            raise RuntimeError("stuck")

    stack = Stack([RecordingLower(), _Tag("a", log), _Fails("b", log)])
    stack.start()
    with pytest.raises(RuntimeError, match="stuck"):
        stack.stop()
    assert log == ["start:a", "start:b", "stop:b", "stop:a"]
    stack.stop()  # already stopped: nothing runs twice
    assert log.count("stop:a") == 1


def test_envelope_is_immutable_and_with_merges():
    env = Envelope(payload="p", headers={"a": "1"}, meta={"m": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.payload = "q"  # type: ignore[misc]
    with pytest.raises(TypeError):
        env.headers["b"] = "2"  # type: ignore[index]
    new = env.with_(headers={"b": "2"}, meta={"n": 2})
    assert dict(new.headers) == {"a": "1", "b": "2"}
    assert dict(new.meta) == {"m": 1, "n": 2}
    assert dict(env.headers) == {"a": "1"}
    assert new.payload == "p"
    assert env.with_(payload=None).payload is None


def test_layers_run_top_down_and_lifecycle_is_bottom_first_then_top_first():
    lower = RecordingLower()
    log: list[str] = []
    a, b = _Tag("a", log), _Tag("b", log)
    stack = Stack([lower, a, b])
    stack.start()
    stack.start()
    assert stack.send(Envelope(payload="x")) == "ok"
    assert lower.seen[0].payload == "x>b>a"
    stack.stop()
    stack.stop()
    assert log == ["start:a", "start:b", "stop:b", "stop:a"]


def test_capabilities_are_the_or_merge_and_layers_cannot_remove_one():
    lower = RecordingLower(unicast=True, e2e=True)
    stack = Stack([lower, _Tag()])
    caps = stack.capabilities()
    assert caps["unicast"] and caps["e2e"] and not caps["group"]


def test_stack_stamps_a_child_traceparent_on_every_send_and_keeps_the_trace_id():
    lower = RecordingLower()
    stack = Stack([lower])
    parent = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"
    stack.send(Envelope(headers={"traceparent": parent}))
    stack.send(Envelope())
    first, second = (e.headers["traceparent"] for e in lower.seen)
    assert first.split("-")[1] == "ab" * 16
    assert first.split("-")[2] != "cd" * 8
    assert second.split("-")[1] != "ab" * 16


def test_async_send_is_stamped_too():
    lower = RecordingLower()
    asyncio.run(Stack([lower]).asend(Envelope()))
    assert lower.seen[0].headers["traceparent"].startswith("00-")


def test_construction_rejects_bad_stacks():
    with pytest.raises(ValueError, match="at least one"):
        Stack([])
    with pytest.raises(TypeError, match="not a Layer"):
        Stack([object()])
    with pytest.raises(TypeError, match="layer 1 .* not a Layer"):
        Stack([RecordingLower(), object()])
    with pytest.raises(ValueError, match="bottom layer .* must be a transport"):
        Stack([_Tag()])
    with pytest.raises(ValueError, match="layer 1 .* is a transport"):
        Stack([RecordingLower(), RecordingLower()])


def test_unbound_layer_raises_and_tag_layer_conforms():
    check_layer(lambda: _Tag("c"))
    with pytest.raises(StackError, match="unbound"):
        _Tag().send(Envelope())
