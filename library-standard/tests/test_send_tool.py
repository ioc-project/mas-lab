# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""SendTool: name-locked legacy tools, allowed peers, one prepare for sync/async."""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_type_hints

import pytest
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.system_tools.send import SendTool, SendToolContext
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, SendContext
from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
from mas.runtime.boundary.delegation.policy import openai_delegation_tools


def _ctx(**fields: object) -> SimpleNamespace:
    values = {
        "agent_id": "",
        "manifest": None,
        "agent_comm": None,
        "agent_comm_routes": None,
        "delegation_peer_descriptions": None,
        "send_peer_descriptions": None,
    }
    values.update(fields)
    return SimpleNamespace(**values)


class _RunTurn:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(
        self,
        agent_id: str,
        task: str,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
    ) -> str:
        self.calls.append((agent_id, task, correlation_id, caller_call_id, context_id))
        return f"ok:{agent_id}:{task}"

    async def asend(
        self,
        agent_id: str,
        task: str,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
    ) -> str:
        return f"async:{agent_id}:{task}"


def _legacy_manifest() -> dict:
    return {
        "metadata": {"name": "moderator"},
        "spec": {
            "workflow": {
                "entry": "moderator",
                "nodes": [
                    {"id": "moderator", "delegates_to": ["worker"]},
                    {"id": "worker"},
                ],
            }
        },
    }


def _two_level_hierarchy_manifest() -> dict:
    return {
        "metadata": {"name": "A"},
        "spec": {
            "workflow": {
                "entry": "A",
                "nodes": [
                    {"id": "A", "delegates_to": ["B", "C", "D"]},
                    {"id": "B"},
                    {"id": "C", "delegates_to": ["E", "F"]},
                    {"id": "D"},
                    {"id": "E"},
                    {"id": "F"},
                ],
            }
        },
    }


def test_delegate_to_schemas_match_openai_delegation_tools() -> None:
    manifest = _legacy_manifest()
    advertised = SendTool.collect_openai_tools(manifest, agent_id="moderator")
    assert advertised == openai_delegation_tools(manifest, agent_id="moderator")
    params = advertised[0]["function"]["parameters"]
    assert params == {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Task for the delegate agent."},
            "context_id": {
                "type": "string",
                "description": (
                    "Optional: continue a specific prior working-memory context with this "
                    "peer instead of the current session's default one — e.g. to run two "
                    "independent conversations with the same specialist in one session. "
                    "Omit to use the session's default context for this peer."
                ),
            },
        },
        "required": ["task"],
    }


def test_list_tools_follow_spec_not_bus_routes() -> None:
    run = _RunTurn()
    routes = {
        "moderator": AgentCommRoute("moderator", "local", LocalAgentComm(run)),
        "worker": AgentCommRoute("worker", "local", LocalAgentComm(run)),
        "stranger": AgentCommRoute("stranger", "a2a", LocalAgentComm(run)),
    }
    tool = SendTool(comm=LocalAgentComm(run), routes=routes)
    names = [spec["name"] for spec in tool.list_tools(manifest=_legacy_manifest())]
    assert names == ["delegate_to_worker"]
    assert "send" not in names


def test_two_level_hierarchy_list_tools_follow_spec_not_bus() -> None:
    run = _RunTurn()
    agents = ["A", "B", "C", "D", "E", "F"]
    routes = {agent_id: AgentCommRoute(agent_id, "local", LocalAgentComm(run)) for agent_id in agents}
    tool = SendTool(comm=LocalAgentComm(run), routes=routes)
    manifest = _two_level_hierarchy_manifest()

    def names(agent_id: str) -> list[str]:
        return [
            spec["name"]
            for spec in tool.list_tools(
                ctx=_ctx(agent_id=agent_id),
                manifest=manifest,
            )
        ]

    assert names("A") == ["delegate_to_B", "delegate_to_C", "delegate_to_D"]
    assert names("C") == ["delegate_to_E", "delegate_to_F"]
    for leaf in ("B", "D", "E", "F"):
        assert names(leaf) == []


def test_delegate_to_ignores_bus_and_rejects_smuggled_target() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["b"])
    out = tool.call_send_tool("delegate_to_b", {"task": "hello", "target": "evil"})
    assert "extra arguments" in out
    assert run.calls == []
    intent = tool.call_send_tool("delegate_to_b", {"task": "hello", "intent": "inform"})
    assert "extra arguments" in intent
    assert run.calls == []
    ok = tool.call_send_tool("delegate_to_b", {"task": "hello"})
    assert ok == "ok:b:hello"
    assert run.calls == [("b", "hello", 0, "", "")]


def test_send_checks_allowed_peers_not_routes() -> None:
    run = _RunTurn()
    stranger = _RunTurn()
    tool = SendTool(
        comm=LocalAgentComm(run),
        routes={"stranger": AgentCommRoute("stranger", "a2a", LocalAgentComm(stranger))},
        allowed_peers=["worker"],
    )
    assert "not an allowed peer" in tool.send("stranger", "no")
    assert stranger.calls == []
    assert tool.send("worker", "yes") == "ok:worker:yes"
    assert "not an allowed peer" in tool.call_send_tool(
        "send", {"target": "evil", "message": "no"}
    )


def test_unknown_intent_is_an_error() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["worker"])
    assert tool.send("worker", "x", intent="nope") == "[send] unknown intent 'nope'"
    assert run.calls == []
    assert tool.call_send_tool("send", {"target": "worker", "message": "x", "intent": "nope"}) == (
        "[send] unknown intent 'nope'"
    )


def test_no_allow_list_reaches_nobody() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run))
    assert "not an allowed peer" in tool.send("worker", "hello")
    assert "not an allowed peer" in tool.call_send_tool("delegate_to_zzz", {"task": "x"})
    assert run.calls == []


def test_send_errors_and_claims() -> None:
    class Boom(LocalAgentComm):
        def send(self, *args, **kwargs):
            raise AgentCommError("down")

    class Missing:
        def send(self, *args, **kwargs):
            raise KeyError("gone")

    class Crash:
        def send(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert "via local-bus failed" in SendTool(
        comm=Boom(lambda *_: ""), allowed_peers=["w"]
    ).send("w", "t")
    assert "not available on bus" in SendTool(
        comm=Missing(), allowed_peers=["w"]  # type: ignore[arg-type]
    ).send("w", "t")
    assert "failed: boom" in SendTool(
        comm=Crash(), allowed_peers=["w"]  # type: ignore[arg-type]
    ).send("w", "t")
    bare = SendTool(allowed_peers=["w"])
    with pytest.raises(AgentCommError, match="no communication plugin"):
        bare._resolve("w")
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["worker"])
    assert tool.send("", "x") == "[send] missing target"
    assert tool.send("worker", "  ") == "[send] missing message for 'worker'"
    assert tool.delegate("worker", "job") == "ok:worker:job"
    assert tool.claims("delegate_to_worker")
    assert not tool.claims("send")
    assert not tool.is_delegate_tool("send")
    assert not tool.claims("spawn_subagent")
    assert tool.call_send_tool("web_search", {}) == "[send] not a send tool: 'web_search'"
    assert tool.on_execute_tool("delegate_to_worker", {"task": "go"}) == "ok:worker:go"
    ctx = _ctx(agent_comm=LocalAgentComm(run), agent_comm_routes={})
    unbound = SendTool(allowed_peers=["worker"])
    assert unbound.call("delegate_to_worker", {"task": "x"}, ctx=ctx).startswith("ok:")


@pytest.mark.asyncio
async def test_asend_uses_same_prepare_including_routes() -> None:
    local = _RunTurn()
    remote_calls: list[tuple[str, str]] = []

    class Remote:
        def send(self, target_agent_id: str, task: str, **_: object) -> str:
            raise AssertionError("sync send must not run when asend exists")

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            remote_calls.append((target_agent_id, task))
            return f"remote:{target_agent_id}:{task}"

    tool = SendTool(
        comm=LocalAgentComm(local),
        routes={"peer": AgentCommRoute("peer", "a2a", Remote())},  # type: ignore[arg-type]
        allowed_peers=["peer", "worker"],
    )
    assert await tool.asend("peer", "job") == "remote:peer:job"
    assert remote_calls == [("peer", "job")]
    assert local.calls == []
    assert await tool.asend("worker", "job") == "async:worker:job"
    assert await tool.asend("stranger", "job") == "[send] target 'stranger' is not an allowed peer"
    assert await tool.asend("", "x") == "[send] missing target"
    assert await tool.asend("worker", " ") == "[send] missing message for 'worker'"
    assert await tool.asend("worker", "x", intent="nope") == "[send] unknown intent 'nope'"
    assert await tool.acall_send_tool("nope", {}) == "[send] not a send tool: 'nope'"
    assert (await tool.acall("delegate_to_worker", {"task": "go"})).startswith("async:")
    smuggled = await tool.acall_send_tool(
        "delegate_to_worker", {"task": "go", "target": "evil"}
    )
    assert "extra arguments" in smuggled

    class SyncOnly:
        def send(self, target, task, **_):
            return f"sync:{target}:{task}"

    sync_tool = SendTool(comm=SyncOnly(), allowed_peers=["w"])  # type: ignore[arg-type]
    assert await sync_tool.asend("w", "t") == "sync:w:t"

    class Boom:
        def send(self, *args, **kwargs):
            raise AgentCommError("down")

        async def asend(self, *args, **kwargs):
            raise AgentCommError("down")

    class Missing:
        def send(self, *args, **kwargs):
            raise KeyError("gone")

        async def asend(self, *args, **kwargs):
            raise KeyError("gone")

    class Crash:
        def send(self, *args, **kwargs):
            raise RuntimeError("boom")

        async def asend(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert "failed" in await SendTool(comm=Boom(), allowed_peers=["w"]).asend("w", "t")  # type: ignore[arg-type]
    assert "not available" in await SendTool(
        comm=Missing(), allowed_peers=["w"]  # type: ignore[arg-type]
    ).asend("w", "t")
    assert "failed: boom" in await SendTool(
        comm=Crash(), allowed_peers=["w"]  # type: ignore[arg-type]
    ).asend("w", "t")
    ctx = _ctx(agent_comm=LocalAgentComm(local))
    unbound = SendTool(allowed_peers=["worker"])
    assert (await unbound.acall("delegate_to_worker", {"task": "z"}, ctx=ctx)).startswith("async:")
    tool.reset_session()
    tool.set_routes({})
    collected = tool.on_collect_tools(
        ctx=_ctx(agent_id="moderator", manifest=_legacy_manifest())
    )
    assert [spec["name"] for spec in collected] == ["delegate_to_worker"]


def test_bind_uses_this_call_not_the_first_session() -> None:
    first = _RunTurn()
    second = _RunTurn()
    tool = SendTool(allowed_peers=["worker"])
    ctx1 = _ctx(agent_comm=LocalAgentComm(first), agent_comm_routes={})
    ctx2 = _ctx(agent_comm=LocalAgentComm(second), agent_comm_routes={})
    assert tool.call("delegate_to_worker", {"task": "one"}, ctx=ctx1) == "ok:worker:one"
    assert tool.call("delegate_to_worker", {"task": "two"}, ctx=ctx2) == "ok:worker:two"
    assert first.calls == [("worker", "one", 0, "", "")]
    assert second.calls == [("worker", "two", 0, "", "")]


def test_non_string_message_is_rejected() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["worker"])
    assert tool.send("worker", {"a": 1}) == "[send] message must be a string"  # type: ignore[arg-type]
    assert tool.call_send_tool("delegate_to_worker", {"task": {"a": 1}}) == (
        "[send] message must be a string"
    )
    assert run.calls == []


def test_plugin_without_headers_or_context_is_called() -> None:
    class Legacy:
        def send(self, target_agent_id: str, task: str) -> str:
            return f"legacy:{target_agent_id}:{task}"

    tool = SendTool(comm=Legacy(), allowed_peers=["w"])  # type: ignore[arg-type]
    assert tool.send("w", "t") == "legacy:w:t"


def test_type_error_inside_peer_is_not_a_tool_result() -> None:
    def run_turn(*_args: object) -> str:
        return len(None)  # type: ignore[arg-type]

    tool = SendTool(comm=LocalAgentComm(run_turn), allowed_peers=["w"])
    with pytest.raises(TypeError):
        tool.send("w", "t")


def test_headers_and_context_reach_a_plugin_that_takes_them() -> None:
    seen: list[dict[str, object]] = []

    class Modern:
        HONOURED_SEND_INTENTS = frozenset({"task", "inform", "ask"})

        def send(
            self,
            target_agent_id: str,
            task: str,
            *,
            correlation_id: int = 0,
            caller_call_id: str = "",
            context_id: str = "",
            headers: dict[str, str] | None = None,
            context: SendContext | None = None,
        ) -> str:
            seen.append(
                {
                    "target": target_agent_id,
                    "task": task,
                    "headers": headers,
                    "context": context,
                }
            )
            return "ok"

    tool = SendTool(comm=Modern(), allowed_peers=["w"])  # type: ignore[arg-type]
    assert tool.send("w", "t", intent="inform", headers={"traceparent": "00"}) == "ok"
    assert seen[0]["target"] == "w"
    assert seen[0]["task"] == "t"
    assert seen[0]["headers"] == {"traceparent": "00"}
    context = seen[0]["context"]
    assert isinstance(context, SendContext)
    assert context.intent == "inform"


def test_session_comm_and_routes_replace_configured() -> None:
    configured = _RunTurn()
    session = _RunTurn()
    tool = SendTool(
        comm=LocalAgentComm(configured),
        routes={"worker": AgentCommRoute("worker", "local", LocalAgentComm(configured))},
        allowed_peers=["worker"],
    )
    ctx = _ctx(
        agent_comm=LocalAgentComm(session),
        agent_comm_routes={"worker": AgentCommRoute("worker", "local", LocalAgentComm(session))},
    )
    assert tool.send("worker", "x", ctx=ctx) == "ok:worker:x"
    assert configured.calls == []
    assert session.calls == [("worker", "x", 0, "", "")]


def test_engine_tool_context_does_not_declare_the_comm_bus() -> None:
    from mas.runtime.boundary.engine_tools import EngineToolContext

    assert "agent_comm" not in EngineToolContext.__dict__
    assert "run_turn" in EngineToolContext.__dict__


def test_send_tool_reads_send_tool_context() -> None:
    assert get_type_hints(SendTool.send)["ctx"] == SendToolContext | None
    assert get_type_hints(SendTool._prepare)["ctx"] == SendToolContext | None


def test_send_peer_descriptions_fill_in_when_delegation_map_is_absent() -> None:
    tool = SendTool(allowed_peers=["worker"])
    specs = tool.on_collect_tools(
        ctx=_ctx(
            agent_id="moderator",
            manifest=_legacy_manifest(),
            send_peer_descriptions={"worker": "Overlay peer description."},
        )
    )
    assert specs[0]["name"] == "delegate_to_worker"
    assert specs[0]["description"] == "Overlay peer description."
