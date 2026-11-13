#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Many sessions in one process do not affect each other.

Each session answers with its own tag. Whether the sessions run on separate
threads, on one event loop, or on one loop per thread, a session must only
ever see its own answers.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time

from mas.ctl.session.controller import ConversationConfig, SessionController
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.ingress import EngineIoReturn

SESSIONS = 8
TURNS = 5


class _Silent:
    def on_user(self, text: str, *, turn_id: str = "") -> None: ...
    def on_agent(self, text: str) -> None: ...
    def on_turn_error(self, message: str, *, detail: str = "") -> None: ...
    def on_hitl_request(self, request: object) -> None: ...
    def on_system(self, message: str) -> None: ...
    def on_error(self, message: str) -> None: ...


class _TaggedEngine(SimulatedEngine):
    """Asks for one tool, then answers with this session's tag and the turn number."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag
        self.turn = 0
        self._tool_done = False

    def _reply(self, io):
        cid = io.correlation_id
        if io.op == "LLM_CALL" and not self._tool_done:
            return EngineIoReturn(
                correlation_id=cid,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name="echo",
                tool_arguments={},
                offered_tools=["echo"],
            )
        if io.op == "LLM_CALL":
            self._tool_done = False
            return EngineIoReturn(
                correlation_id=cid,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text=f"answer:{self.tag}:{self.turn}",
            )
        if io.op == "TOOL_CALL":
            self._tool_done = True
            return EngineIoReturn(
                correlation_id=cid, response_kind="TOOL_RESULT", next_step="LLM_CALL", text=f"tool:{self.tag}"
            )
        return EngineIoReturn(correlation_id=cid, response_kind="ERROR", next_step="STOP")

    def invoke(self, io):
        time.sleep(random.random() * 0.005)
        return self._reply(io)

    async def ainvoke(self, io):
        await asyncio.sleep(random.random() * 0.005)
        return self._reply(io)


def _sessions() -> list[tuple[str, SessionController, _TaggedEngine]]:
    out = []
    for i in range(SESSIONS):
        tag = f"s{i}"
        engine = _TaggedEngine(tag)
        instance = RuntimeInstance.from_parts(engine=engine, config=KernelConfig())
        instance.capture_session_baseline()
        controller = SessionController(
            instance=instance,
            display=_Silent(),
            config=ConversationConfig(single_turn=False),
            session_id=f"sid-{tag}",
        )
        out.append((tag, controller, engine))
    return out


def _check(tag: str, turn: int, result, problems: list[str]) -> None:
    text = result.text
    if text != f"answer:{tag}:{turn}":
        problems.append(f"{tag} turn {turn}: {text!r}")


def test_sessions_on_separate_threads_stay_apart():
    problems: list[str] = []

    def run(tag, controller, engine):
        for turn in range(TURNS):
            engine.turn = turn
            _check(tag, turn, controller.run_turn(f"q{turn}"), problems)

    threads = [threading.Thread(target=run, args=s) for s in _sessions()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert problems == []


def test_sessions_on_one_event_loop_stay_apart():
    problems: list[str] = []

    async def run(tag, controller, engine):
        for turn in range(TURNS):
            engine.turn = turn
            _check(tag, turn, await controller.arun_turn(f"q{turn}"), problems)

    async def main():
        await asyncio.gather(*(run(*s) for s in _sessions()))

    asyncio.run(main())
    assert problems == []


def test_sessions_each_on_their_own_loop_and_thread_stay_apart():
    problems: list[str] = []

    def run(tag, controller, engine):
        async def go():
            for turn in range(TURNS):
                engine.turn = turn
                _check(tag, turn, await controller.arun_turn(f"q{turn}"), problems)

        asyncio.run(go())

    threads = [threading.Thread(target=run, args=s) for s in _sessions()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert problems == []
