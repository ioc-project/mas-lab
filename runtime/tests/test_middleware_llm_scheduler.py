#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from mas.library.standard.plugins.infra.llm_cache import LlmCacheMiddleware
from mas.library.standard.plugins.infra.llm_scheduler import LlmSchedulerMiddleware
from mas.runtime.engine.llm_scheduler import (
    SchedulerPolicy,
    install_policy,
    llm_slot,
    reset_policy,
)
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


@dataclass
class _SlowEngine:
    delay_s: float = 0.15
    calls: int = 0
    peak: int = 0
    _inflight: int = 0
    model: str = "gpt-test"

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        return f"preview:{op}:{correlation_id}"

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        self._inflight += 1
        self.peak = max(self.peak, self._inflight)
        self.calls += 1
        time.sleep(self.delay_s)
        self._inflight -= 1
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text=f"live-{self.calls}",
        )


@pytest.fixture(autouse=True)
def _reset_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAS_LLM_SCHEDULER_STATE", str(tmp_path / "sched.json"))
    monkeypatch.delenv("MAS_LLM_MAX_IN_FLIGHT", raising=False)
    monkeypatch.delenv("MAS_LLM_RPM", raising=False)
    monkeypatch.delenv("MAS_LLM_SCHEDULER_USER", raising=False)
    reset_policy()
    yield
    reset_policy()


def test_scheduler_caps_in_flight(tmp_path: Path) -> None:
    inner = _SlowEngine()
    engine = LlmSchedulerMiddleware.wrap(
        inner,
        {
            "max_in_flight": 1,
            "requests_per_minute": 0,
            "state_path": str(tmp_path / "sched.json"),
            "timeout_s": 5,
        },
    )
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            engine.invoke(io)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert inner.calls == 3
    assert inner.peak == 1


def test_cache_outer_skips_scheduler(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    inner = _SlowEngine(delay_s=0.0)
    scheduled = LlmSchedulerMiddleware.wrap(
        inner,
        {
            "max_in_flight": 1,
            "state_path": str(tmp_path / "sched.json"),
        },
    )
    engine = LlmCacheMiddleware.wrap(scheduled, {"cache_path": str(cache_path)})
    io = InvokeEngineIo(correlation_id=1, op="LLM_CALL")
    first = engine.invoke(io)
    second = engine.invoke(io)
    assert first.text == second.text == "live-1"
    assert inner.calls == 1


def test_llm_slot_noops_without_policy() -> None:
    reset_policy()
    with llm_slot(model="x") as key:
        assert key is None


def test_max_in_flight_zero_is_unlimited(tmp_path: Path) -> None:
    policy = SchedulerPolicy.from_params(
        {
            "max_in_flight": 0,
            "requests_per_minute": 0,
            "state_path": str(tmp_path / "sched.json"),
        }
    )
    assert policy.max_in_flight == 0
    assert not policy.enabled()
    install_policy(policy)
    with llm_slot(model="x", policy=policy) as key:
        assert key is None


def test_policy_keys_shard_by_model(tmp_path: Path) -> None:
    policy = SchedulerPolicy.from_params(
        {
            "max_in_flight": 1,
            "keys": ["model"],
            "state_path": str(tmp_path / "sched.json"),
        }
    )
    install_policy(policy)
    started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []

    def _a() -> None:
        with llm_slot(model="a", policy=policy):
            order.append("a-in")
            started.set()
            release_first.wait(timeout=2)
            order.append("a-out")

    def _b() -> None:
        started.wait(timeout=2)
        with llm_slot(model="b", policy=policy):
            order.append("b")

    ta = threading.Thread(target=_a)
    tb = threading.Thread(target=_b)
    ta.start()
    tb.start()
    started.wait(timeout=2)
    time.sleep(0.05)
    assert "b" in order
    release_first.set()
    ta.join()
    tb.join()


_MP_HOLD = """
import json, sys, time
from pathlib import Path
from mas.runtime.engine.llm_scheduler import SchedulerPolicy, install_policy, llm_slot
state, out, delay = sys.argv[1], sys.argv[2], float(sys.argv[3])
policy = SchedulerPolicy.from_params({
    "max_in_flight": 1,
    "state_path": state,
    "timeout_s": 10,
    "keys": ["model"],
})
install_policy(policy)
with llm_slot(model="shared", policy=policy):
    held = {"start": time.time(), "end": 0.0}
    time.sleep(delay)
    held["end"] = time.time()
Path(out).write_text(json.dumps(held), encoding="utf-8")
"""


def test_acquire_times_out_when_slot_held(tmp_path: Path) -> None:
    from mas.runtime.engine.llm_scheduler import acquire

    policy = SchedulerPolicy.from_params(
        {
            "max_in_flight": 1,
            "timeout_s": 0.2,
            "state_path": str(tmp_path / "sched.json"),
            "keys": ["model"],
        }
    )
    with llm_slot(model="x", policy=policy):
        with pytest.raises(TimeoutError, match="llm_scheduler timed out"):
            acquire(model="x", policy=policy)


def test_dead_pid_lease_does_not_block(tmp_path: Path) -> None:
    state_path = tmp_path / "sched.json"
    state_path.write_text(
        json.dumps(
            {
                "buckets": {
                    "model=x": {
                        "tokens": 0,
                        "updated": time.time(),
                        "leases": [{"pid": 2**31 - 1, "since": time.time()}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    policy = SchedulerPolicy.from_params(
        {
            "max_in_flight": 1,
            "state_path": str(state_path),
            "keys": ["model"],
            "timeout_s": 30,
        }
    )
    with llm_slot(model="x", policy=policy) as key:
        assert key == "model=x"


def test_install_policy_from_pipeline(tmp_path: Path) -> None:
    from mas.runtime.engine.llm_scheduler import current_policy, install_policy_from_pipeline

    policy = install_policy_from_pipeline(
        [
            {
                "middleware": "llm_scheduler",
                "params": {
                    "max_in_flight": 2,
                    "state_path": str(tmp_path / "sched.json"),
                },
            }
        ]
    )
    assert policy is not None
    assert policy.max_in_flight == 2
    assert current_policy() is policy


def test_multiprocess_shares_file_state(tmp_path: Path) -> None:
    state_path = tmp_path / "sched.json"
    a_out = tmp_path / "a.json"
    b_out = tmp_path / "b.json"
    cmd = [sys.executable, "-c", _MP_HOLD]
    procs = [
        subprocess.Popen([*cmd, str(state_path), str(a_out), "0.2"]),
        subprocess.Popen([*cmd, str(state_path), str(b_out), "0.2"]),
    ]
    for proc in procs:
        assert proc.wait(timeout=10) == 0
    a = json.loads(a_out.read_text(encoding="utf-8"))
    b = json.loads(b_out.read_text(encoding="utf-8"))
    overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
    assert overlap <= 0.05
