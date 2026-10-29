#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pending steers on InflightStore: replace and preempt exclude each other."""

from __future__ import annotations

from mas.runtime.engine.inflight_llm import InflightStore


def test_request_replace_discards_a_pending_preempt() -> None:
    store = InflightStore()
    store.request_preempt("s1", "keep going")
    store.request_replace("s1", "start over")
    assert store.peek_replace("s1") == "start over"
    assert store.peek_preempt("s1") is None


def test_request_preempt_discards_a_pending_replace() -> None:
    store = InflightStore()
    store.request_replace("s1", "start over")
    store.request_preempt("s1", "keep going")
    assert store.peek_preempt("s1") == "keep going"
    assert store.peek_replace("s1") is None


def test_clear_keeps_partials_while_a_preempt_is_pending() -> None:
    store = InflightStore()
    store.append_partial("s1", "Hello ")
    store.request_preempt("s1", "keep going")
    store.clear("s1")
    assert store.take_partial("s1") == "Hello "


def test_clear_drops_partials_when_no_steer_is_pending() -> None:
    store = InflightStore()
    store.append_partial("s1", "Hello ")
    store.clear("s1")
    assert store.take_partial("s1") == ""


def test_request_replace_discards_partial_text() -> None:
    store = InflightStore()
    store.append_partial("s1", "Hello ")
    store.request_replace("s1", "start over")
    assert store.take_partial("s1") == ""


def test_module_wrappers_use_the_same_store() -> None:
    from mas.runtime.engine import inflight_llm

    inflight_llm.store.request_preempt("s-wrap", "keep going")
    try:
        assert inflight_llm.peek_preempt("s-wrap") == "keep going"
    finally:
        inflight_llm.store.clear("s-wrap")
        inflight_llm.store.pop_preempt("s-wrap")


def test_cancel_of_a_finished_task_returns_false() -> None:
    store = InflightStore()

    class _Done:
        def done(self) -> bool:
            return True

        def cancel(self) -> None:
            raise AssertionError("must not cancel a finished task")

    store.register("s1", _Done())  # type: ignore[arg-type]
    assert store.cancel("s1") is False
