#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""M_spec: the one place that decides whether a spec_delta applies."""

from __future__ import annotations

import pytest
from mas.runtime.session import SessionStatus, SpecVerdict, spec_on_delta

_LIVE = (SessionStatus.CREATED, SessionStatus.ACTIVE, SessionStatus.PAUSED)
_CLOSED = (SessionStatus.CHECKPOINTING, SessionStatus.TERMINATED)


@pytest.mark.parametrize("status", _LIVE)
def test_a_live_session_applies_an_unguarded_delta(status: SessionStatus) -> None:
    assert spec_on_delta(status, {"n": 1}) is SpecVerdict.APPLY


@pytest.mark.parametrize("status", _CLOSED)
def test_a_session_being_checkpointed_or_over_is_busy_whatever_the_delta_expects(status: SessionStatus) -> None:
    assert spec_on_delta(status, {"n": 1}) is SpecVerdict.BUSY
    assert spec_on_delta(status, {"n": 1}, {"n": 1}, guarded=True) is SpecVerdict.BUSY


def test_a_guarded_delta_applies_only_to_the_state_it_expected() -> None:
    live = SessionStatus.ACTIVE
    assert spec_on_delta(live, {"n": 1}, {"n": 1}, guarded=True) is SpecVerdict.APPLY
    assert spec_on_delta(live, {"n": 2}, {"n": 1}, guarded=True) is SpecVerdict.CONFLICT
    assert spec_on_delta(live, None, {}, guarded=True) is SpecVerdict.APPLY  # no state is the empty state
    assert spec_on_delta(live, {"n": 1}, None, guarded=True) is SpecVerdict.CONFLICT
    assert spec_on_delta(live, "breaker", "breaker", guarded=True) is SpecVerdict.APPLY  # an owner tag
    assert spec_on_delta(live, "operator", "breaker", guarded=True) is SpecVerdict.CONFLICT
    assert spec_on_delta(live, None, "breaker", guarded=True) is SpecVerdict.CONFLICT


def test_an_unguarded_delta_ignores_the_expected_state() -> None:
    assert spec_on_delta(SessionStatus.ACTIVE, {"n": 9}, {"n": 1}) is SpecVerdict.APPLY


def test_concurrent_guarded_writers_never_both_pass() -> None:
    """Many threads each read a counter and try to raise it by one; every success moves it by exactly one."""
    import threading

    from mas.runtime.session.spec_machine import SpecConflict, SpecVerdict, spec_on_delta
    from mas.runtime.session.spec_revision import LAYER_STATE_KEY, SpecDelta, SpecRevisionLog
    from mas.runtime.session.state import SessionStatus

    log = SpecRevisionLog()
    log.materialize("s", {"spec": {}})
    wins: list[int] = []

    def bump() -> None:
        for _ in range(40):
            seen = log.layer_state("s", "L").get("t", {}).get("n", 0)

            def decide(spec: dict, seen: int = seen) -> None:
                current = ((spec.get(LAYER_STATE_KEY) or {}).get("L") or {}).get("t")
                expected = {"n": seen} if seen else None
                if spec_on_delta(SessionStatus.ACTIVE, current, expected, guarded=True) is SpecVerdict.CONFLICT:
                    raise SpecConflict("moved")

            try:
                log.apply(
                    "s",
                    SpecDelta("swap_layer_state", {"layer": "L", "target": "t", "state": {"n": seen + 1}}),
                    actor="t",
                    reason="bump",
                    decide=decide,
                )
            except SpecConflict:
                continue
            wins.append(seen + 1)

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert log.layer_state("s", "L")["t"]["n"] == len(wins)
    assert sorted(wins) == list(range(1, len(wins) + 1))  # no value was written twice
    assert len(log._revs["s"]) == len(wins) + 1


def test_no_write_lands_while_the_log_is_sealed() -> None:
    """A checkpoint seals the log: a writer that decided earlier loses its claim and is decided again."""
    import pytest
    from mas.runtime.session.spec_machine import SpecConflict, SpecVerdict, spec_on_delta
    from mas.runtime.session.spec_revision import SpecDelta, SpecRevisionLog
    from mas.runtime.session.state import SessionStatus

    log = SpecRevisionLog()
    log.materialize("s", {"spec": {"tools": [{"name": "t"}]}})
    status = {"now": SessionStatus.ACTIVE}
    seen: list[SessionStatus] = []

    def decide(spec: dict) -> None:
        seen.append(status["now"])
        verdict = spec_on_delta(status["now"], None)
        status["now"] = SessionStatus.CHECKPOINTING  # the checkpoint starts right after this decision
        if verdict is SpecVerdict.BUSY:
            raise SpecConflict("busy")

    delta = SpecDelta("disable_tool", {"name": "t"})
    with log.sealed("s") as (manifest, revision):
        with pytest.raises(SpecConflict):
            log.apply("s", delta, actor="t", reason="late", decide=decide)
        assert revision == 0 and "enabled" not in manifest["spec"]["tools"][0]
        assert log.current_spec("s")["tools"][0].get("enabled") is not False
        assert seen == [SessionStatus.ACTIVE, SessionStatus.CHECKPOINTING]  # decided again after losing the claim
    status["now"] = SessionStatus.ACTIVE
    rev = log.apply("s", delta, actor="t", reason="after", decide=decide)
    assert rev.revision == 1 and seen[-1] is SessionStatus.ACTIVE
