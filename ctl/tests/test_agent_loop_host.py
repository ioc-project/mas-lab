#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``spec.loop``: an agent served over A2A and run by the lab, backed by a stub external harness.

The stub (``tests/support/stub_harness.py``) is a standard-library script speaking the stdio task protocol.
MAS Lab keeps the boundary: the A2A listener, the Controls (a ``budget`` Control here) and observability.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest
import yaml
from mas.ctl.manifest.spec_bindings import SpecBindingError, parse_loop, validate_agent_spec_bindings
from mas.ctl.session.exposure import close_exposures, expose_agent
from mas.ctl.session.flavour import resolve_flavour
from mas.ctl.session.loop import loop_binding, loop_from_spec
from mas.ctl.validate import validate_data
from mas.runtime.boundary.loop import LoopDenied, LoopTask

STUB = str(Path(__file__).resolve().parents[2] / "tests" / "support" / "stub_harness.py")
PY = os.path.realpath(sys.executable)


def agent(name: str = "front", framing: str = "line") -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": name},
        "spec": {
            "description": "Fronts an external harness.",
            "loop": {
                "type": "stdio-harness",
                "params": {"command": [PY, STUB, framing], "allow": [PY], "framing": framing, "timeout": 20},
            },
        },
    }


def budget_flavour(tmp_path: Path, calls: int) -> dict:
    (tmp_path / "cap.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "control/v1",
                "kind": "Control",
                "metadata": {"name": "cap"},
                "spec": {"governance": [{"budget": {"max_tool_calls": calls}}]},
            }
        ),
        encoding="utf-8",
    )
    flavour = tmp_path / "flavour.yaml"
    flavour.write_text(
        "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: cap}\n"
        "spec:\n  control:\n    - {ref: cap.yaml, agents: '$all'}\n",
        encoding="utf-8",
    )
    return resolve_flavour(str(flavour))


class Collector:
    def __init__(self) -> None:
        self.transitions: list = []

    def on_transition(self, transition) -> None:
        self.transitions.append(transition)

    def loop_calls(self) -> list[str]:
        return [
            t.attributes["phase"]
            for t in self.transitions
            if t.boundary_kind == "session" and t.mealy_symbol == "loop_call"
        ]


def test_no_loop_named_means_the_kernel_loop() -> None:
    assert loop_binding({"spec": {"description": "x"}}) is None
    assert loop_binding({"spec": {"loop": "a2a-harness"}}) == {"type": "a2a-harness"}


def test_spec_loop_is_validated_by_the_schema_and_the_registry() -> None:
    assert validate_data(agent(), kind="agent", strict=True, resolve_refs=False).ok
    bad = agent()
    bad["spec"]["loop"] = {"params": {}}
    assert not validate_data(bad, kind="agent", strict=True, resolve_refs=False).ok
    validate_agent_spec_bindings(agent()["spec"])
    parse_loop("a2a-harness")
    with pytest.raises(SpecBindingError, match="not a registered agent_loop"):
        parse_loop("a2a_harnes")
    with pytest.raises(SpecBindingError, match="unknown"):
        parse_loop({"type": "stdio-harness", "extra": 1})


def test_the_loop_runs_behind_the_agents_controls_and_records_boundary_events(tmp_path: Path) -> None:
    host = loop_from_spec(agent(framing="jsonrpc"), manifest_dir=tmp_path, flavour_spec=budget_flavour(tmp_path, 1))
    collector = Collector()
    host.shell.driver.observability.subscribe(collector)
    try:
        reply = host.loop.run(LoopTask(text="hello", session_id="s1", task_id="t1"))
        assert reply.text == "echo:hello"
        assert reply.events[0]["name"] == "grep"
        with pytest.raises(LoopDenied) as raised:
            host.loop.run(LoopTask(text="again", session_id="s1", task_id="t2"))
        assert raised.value.decision == "TERMINATE"
        assert "budget" in raised.value.policy
    finally:
        host.close()
    assert collector.loop_calls() == ["start", "end", "start", "denied"]
    loop_events = [t for t in collector.transitions if t.mealy_symbol == "loop_event"]
    assert [t.attributes["loop"] for t in loop_events] == ["stdio-harness"]


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def test_a_mas_lab_agent_served_over_a2a_is_backed_by_the_stub_and_gated_at_the_boundary(tmp_path: Path) -> None:
    from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    port = _port()
    url = f"http://127.0.0.1:{port}"
    host = loop_from_spec(agent(), manifest_dir=tmp_path, flavour_spec=budget_flavour(tmp_path, 2))
    exposure = expose_agent(
        None,
        {"metadata": {"name": "front"}, "spec": {"description": "front"}},
        {"protocol": "a2a", "usage": "deploy", "url": url},
        loop=host.loop,
    )
    comm = A2AAgentComm(url=url, timeout=20)
    try:
        assert await comm.asend("front", "ping", context_id="conv-1") == "echo:ping"
        assert await comm.asend("front", "multi\nline", context_id="conv-1") == "echo:multi\nline"
        # the Control's third call is refused at the boundary: the harness never sees it, the caller sees a failed task
        with pytest.raises(AgentCommError):
            await comm.asend("front", "third", context_id="conv-1")
    finally:
        comm.stop()
        close_exposures([exposure])
        host.close()


def _write_agent(tmp_path: Path) -> Path:
    path = tmp_path / "front.yaml"
    path.write_text(yaml.safe_dump(agent()), encoding="utf-8")
    return path


def test_the_lab_loop_drives_the_stdio_harness_in_a_tiny_experiment(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner

    spec_path = _write_agent(tmp_path)
    out = tmp_path / "run-1"
    out.mkdir()
    result = MasBenchRunner().run(
        "what is 2+2?",
        config=yaml.safe_load(spec_path.read_text(encoding="utf-8")),
        spec_path=spec_path,
        output_dir=out,
        flavour=budget_flavour(tmp_path, 5),
        run_seed=3,
    )
    assert result.status == "ok", result.error
    assert result.content == "echo:what is 2+2?"
    assert result.metadata["loop"] == "stdio-harness"
    assert result.metadata["turns"] == 1
    events = [json.loads(line) for line in (out / "traces" / "events.jsonl").read_text().splitlines() if line]
    (start,) = [e for e in events if e["kind"] == "execution_start"]
    assert start["loop"] == "stdio-harness"
    assert start["input"] == "what is 2+2?"
    assert [e["decision"] for e in events if e["kind"] == "governance_decision"] == ["ALLOW", "ALLOW"]
    (response,) = [e for e in events if e["kind"] == "user_response"]
    assert response["content"] == "echo:what is 2+2?"


def test_the_lab_loop_reports_a_refused_task_as_an_error_result(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner

    spec_path = _write_agent(tmp_path)
    out = tmp_path / "run-2"
    out.mkdir()
    result = MasBenchRunner().run(
        "hello",
        config=yaml.safe_load(spec_path.read_text(encoding="utf-8")),
        spec_path=spec_path,
        output_dir=out,
        flavour=budget_flavour(tmp_path, 0),
    )
    assert result.status == "error"
    assert "TERMINATE" in (result.error or "")


def test_serve_with_a_loop_binds_no_kernel_and_hands_the_loop_to_the_listener(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from mas.ctl.cli.commands import serve as serve_cmd
    from mas.ctl.session.observability import ObservabilityConfig

    session = SimpleNamespace(manifest_dir=tmp_path, original_cwd=tmp_path)
    obs = ObservabilityConfig(enabled=False)
    endpoint = {"protocol": "a2a", "usage": "deploy", "url": "http://127.0.0.1:9"}
    served: list = []

    def fake_expose(instance, manifest, ep, *, blocking, loop):
        served.append((instance, ep, blocking, loop.loop_id, loop.run(LoopTask(text="hi")).text))

    with patch.object(serve_cmd, "expose_agent", fake_expose):
        serve_cmd._serve_with_loop(agent(), endpoint, session=session, flavour_spec={}, obs_config=obs, replaced=False)
    assert served == [(None, endpoint, True, "stdio-harness", "echo:hi")]


def test_a_loop_the_arguments_refuse_is_a_value_error_and_an_error_result(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner

    bad = agent()
    bad["spec"]["loop"]["params"]["allow"] = ["/bin/true"]
    with pytest.raises(ValueError, match="not in allow"):
        loop_from_spec(bad, manifest_dir=tmp_path)
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    out = tmp_path / "run-3"
    out.mkdir()
    result = MasBenchRunner().run("hello", config=bad, spec_path=path, output_dir=out, flavour={})
    assert result.status == "error"
    assert "not in allow" in (result.error or "")


def test_the_kernel_loop_reply_keeps_chunks_artifacts_and_task_state() -> None:
    from types import SimpleNamespace

    from mas.ctl.session.loop import handler_result, reply_from_trace

    streamed = SimpleNamespace(
        client_responses=[SimpleNamespace(content="ab", stream_chunks=("a", "b"))], awaiting_hitl=False
    )
    assert handler_result(reply_from_trace(streamed, "s")) == ("a", "b")
    plain = SimpleNamespace(
        client_responses=[
            SimpleNamespace(
                content="one", artifacts=({"kind": "data", "name": "r", "data": {}},), task_state="working"
            ),
            SimpleNamespace(content="two", task_state="completed"),
        ],
        awaiting_hitl=False,
    )
    assert handler_result(reply_from_trace(plain, "s")) == {
        "text": "one\ntwo",
        "context_id": "s",
        "artifacts": [{"kind": "data", "name": "r", "data": {}}],
        "task_state": "completed",
    }
    waiting = SimpleNamespace(client_responses=[], awaiting_hitl=True)
    assert handler_result(reply_from_trace(waiting, "s"))["task_state"] == "input_required"
