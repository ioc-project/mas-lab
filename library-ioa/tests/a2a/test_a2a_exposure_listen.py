#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Two A2A exposures in one process keep distinct agent cards."""

from __future__ import annotations

import socket
import time

import httpx
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.webserver import UvicornWebServer


def _hold_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            sockets.append(listener)
            ports.append(int(listener.getsockname()[1]))
    finally:
        for listener in sockets:
            listener.close()
    return ports


def _wait(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_exc: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05)
    raise RuntimeError(f"server did not listen on 127.0.0.1:{port}") from last_exc


def test_two_background_exposures_serve_distinct_agent_cards() -> None:
    port_assistant, port_greeter = _hold_ports(2)
    webserver = UvicornWebServer()
    assistant = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "use-and-deploy",
            "url": f"http://127.0.0.1:{port_assistant}",
        },
        webserver=webserver,
    )
    greeter = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "usage": "use-and-deploy",
            "url": f"http://127.0.0.1:{port_greeter}",
        },
        webserver=webserver,
    )
    handle_assistant = assistant.serve_background(
        {"metadata": {"name": "assistant"}, "spec": {"description": "entry"}},
        lambda prompt, **_: {"text": "ok"},
    )
    handle_greeter = greeter.serve_background(
        {"metadata": {"name": "greeter"}, "spec": {"description": "peer"}},
        lambda prompt, **_: {"text": "ok"},
    )
    try:
        _wait(port_assistant)
        _wait(port_greeter)
        assistant_card = httpx.get(
            f"http://127.0.0.1:{port_assistant}/.well-known/agent-card.json",
            timeout=5,
        )
        greeter_card = httpx.get(
            f"http://127.0.0.1:{port_greeter}/.well-known/agent-card.json",
            timeout=5,
        )
        assert assistant_card.status_code == 200
        assert greeter_card.status_code == 200
        assert assistant_card.json()["name"] == "assistant"
        assert greeter_card.json()["name"] == "greeter"
    finally:
        handle_assistant.close()
        handle_greeter.close()


def test_a2a_agent_comm_reaches_an_in_process_peer() -> None:
    (port,) = _hold_ports(1)
    url = f"http://127.0.0.1:{port}"
    received: list[str] = []
    handle = A2AExposure(
        endpoint={"protocol": "a2a", "usage": "deploy", "url": url},
        webserver=UvicornWebServer(),
    ).serve_background(
        {"metadata": {"name": "peer"}, "spec": {"description": "peer"}},
        lambda prompt, **_: received.append(prompt) or {"text": f"peer:{prompt}"},
    )
    comm = A2AAgentComm(url=url, timeout=10)
    try:
        _wait(port)
        assert comm.send("peer", "hello") == "peer:hello"
        assert received == ["hello"]
    finally:
        comm.close()
        handle.close()


def test_peer_followups_reuse_recipient_context_over_http() -> None:
    from mas.runtime.boundary.agentcomm.protocol import bound_conversation

    (port,) = _hold_ports(1)
    url = f"http://127.0.0.1:{port}"
    received = []

    def respond(prompt, **metadata):
        received.append((prompt, metadata))
        return {"text": "answer"}

    handle = A2AExposure(
        endpoint={"protocol": "a2a", "usage": "listen", "url": url}, webserver=UvicornWebServer()
    ).serve_background(
        {"metadata": {"name": "peer"}, "spec": {"description": "peer"}},
        respond,
    )
    comm = A2AAgentComm(url=url, timeout=10, conversation="per_relationship")
    try:
        _wait(port)
        with bound_conversation("incoming", "caller"):
            comm.send("peer", "assignment")
            comm.send("peer", "follow up")
        first, second = [metadata for _, metadata in received]
        assert first["session_id"] == second["session_id"] != "incoming"
        assert first["turn_id"] != second["turn_id"]
    finally:
        comm.close()
        handle.close()


def test_nested_runtime_followups_keep_separate_peer_histories() -> None:
    import pytest
    from mas.runtime.boundary.agentcomm.protocol import bound_conversation
    from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryConfig
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.simulated import SimulatedEngine
    from mas.runtime.schema.ingress import EngineIoReturn

    make_runtime_handler = pytest.importorskip("mas.ctl.session.exposure").make_runtime_handler
    ports = _hold_ports(2)
    handles, links, observed = [], {}, {"coordinator": [], "worker": []}

    def receiver(name):
        engine = SimulatedEngine(llm_next_step=lambda _: "STOP")
        instance = RuntimeInstance.from_parts(engine=engine)
        instance.driver.agent_id = name
        instance.working_memory = WorkingMemoryConfig(persistent=True)
        original = engine.invoke

        def invoke(io):
            if io.op != "LLM_CALL":
                return original(io)
            observed[name].append(list(instance.driver.ctx.committed_messages))
            if name == "coordinator":
                links["worker"].send("worker", "lookup" if len(observed[name]) == 1 else "additional detail")
            return EngineIoReturn(
                correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text="answer"
            )

        engine.invoke = invoke
        return make_runtime_handler(instance, manifest={"metadata": {"name": name}})

    try:
        for name, port in zip(("worker", "coordinator"), ports, strict=True):
            url = f"http://127.0.0.1:{port}"
            handles.append(
                A2AExposure(
                    endpoint={"protocol": "a2a", "usage": "listen", "url": url},
                    webserver=UvicornWebServer(),
                ).serve_background({"metadata": {"name": name}}, receiver(name))
            )
            _wait(port)
            links[name] = A2AAgentComm(url=url, timeout=10, conversation="per_relationship")
        with bound_conversation("incoming", "planner"):
            links["coordinator"].send("coordinator", "assignment")
            links["coordinator"].send("coordinator", "detail")
        assert any(m.get("content") == "assignment" for m in observed["coordinator"][1])
        assert any(m.get("content") == "lookup" for m in observed["worker"][1])
        assert not any(m.get("content") == "assignment" for m in observed["worker"][1])
        contexts = [next(iter(link._conversations._relationships.values())).context_id for link in links.values()]
        assert len(set(contexts)) == 2 and "incoming" not in contexts
    finally:
        for link in links.values():
            link.close()
        for handle in reversed(handles):
            handle.close()
