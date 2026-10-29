#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from concurrent.futures import ThreadPoolExecutor

from mas.ctl.session.mailbox import SessionTurnMailbox


def test_two_callers_same_session_run_fifo() -> None:
    mailbox = SessionTurnMailbox()
    seen: list[str] = []

    def run(text: str) -> str:
        seen.append(text)
        return f"ok:{text}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(mailbox.submit, "s1", "a", run=run)
        second = pool.submit(mailbox.submit, "s1", "b", run=run)
        assert {first.result(), second.result()} == {"ok:a", "ok:b"}
    assert set(seen) == {"a", "b"}


def test_missing_session_id_mints_distinct_sessions() -> None:
    mailbox = SessionTurnMailbox()
    a = mailbox.resolve_session_id("")
    b = mailbox.resolve_session_id("")
    assert a != b
    assert mailbox.resolve_session_id("keep") == "keep"


def test_mailbox_submit_does_not_amend_inflight() -> None:
    from mas.runtime.engine import inflight_llm

    mailbox = SessionTurnMailbox()
    inflight_llm.register(
        "s1", type("T", (), {"done": lambda self: False, "cancel": lambda self: None})()
    )
    try:
        assert mailbox.submit("s1", "extra", source="a2a", run=lambda text: text) == "extra"
        assert inflight_llm.peek_amend("s1") is None
        assert mailbox.queue.peek("s1") == []
    finally:
        inflight_llm.clear("s1")
        inflight_llm.pop_preempt("s1")
