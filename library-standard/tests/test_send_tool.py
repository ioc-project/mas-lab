# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""SendTool: leftover delegate_to_* names, one prepare for sync/async."""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_type_hints

import pytest
from mas.library.standard.lib.stack.layer import LinkIdentity
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
        "spec": {"communication": {"peers": ["worker"]}},
    }


_TWO_LEVEL_PEERS = {"A": ["B", "C", "D"], "C": ["E", "F"]}


def _two_level_hierarchy_manifest(agent_id: str = "A") -> dict:
    """The manifest one agent of a two-level hierarchy receives: its own next hops, nothing of the others."""
    return {
        "metadata": {"name": agent_id},
        "spec": {"communication": {"peers": list(_TWO_LEVEL_PEERS.get(agent_id, []))}},
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


async def test_list_tools_empty_when_delegation_style_is_none() -> None:
    manifest = {
        "metadata": {"name": "moderator"},
        "spec": {
            "behavior": {"delegation_style": "none"},
            "communication": {"peers": ["worker"]},
        },
    }
    tool = SendTool(comm=LocalAgentComm(_RunTurn()), allowed_peers=["worker"])
    assert tool.list_tools(manifest=manifest) == []
    assert openai_delegation_tools(manifest, agent_id="moderator") == []
    assert (await tool.asend("worker", "hello")) == "ok:worker:hello"
    assert tool.offers("delegate_to_worker")
    assert not tool.offers("delegate_to_stranger")


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

    def names(agent_id: str) -> list[str]:
        return [
            spec["name"]
            for spec in tool.list_tools(
                ctx=_ctx(agent_id=agent_id),
                manifest=_two_level_hierarchy_manifest(agent_id),
            )
        ]

    assert names("A") == ["delegate_to_B", "delegate_to_C", "delegate_to_D"]
    assert names("C") == ["delegate_to_E", "delegate_to_F"]
    for leaf in ("B", "D", "E", "F"):
        assert names(leaf) == []


async def test_delegate_to_ignores_bus_and_rejects_smuggled_target() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["b"])
    out = await tool.acall_send_tool("delegate_to_b", {"task": "hello", "target": "evil"})
    assert "extra arguments" in out
    assert run.calls == []
    intent = await tool.acall_send_tool("delegate_to_b", {"task": "hello", "intent": "inform"})
    assert "extra arguments" in intent
    assert run.calls == []
    ok = await tool.acall_send_tool("delegate_to_b", {"task": "hello"})
    assert ok == "ok:b:hello"
    assert run.calls == [("b", "hello", 0, "", "")]


async def test_send_delivers_even_when_dest_is_not_offered() -> None:
    """delegates_to / allowed_peers is list_tools, not an execute lock."""
    run = _RunTurn()
    stranger = _RunTurn()
    tool = SendTool(
        comm=LocalAgentComm(run),
        routes={"stranger": AgentCommRoute("stranger", "a2a", LocalAgentComm(stranger))},
        allowed_peers=["worker"],
    )
    assert tool.offers("delegate_to_worker")
    assert not tool.offers("delegate_to_stranger")
    assert (await tool.asend("stranger", "no")) == "ok:stranger:no"
    assert stranger.calls == [("stranger", "no", 0, "", "")]
    assert run.calls == []
    assert (await tool.asend("worker", "yes")) == "ok:worker:yes"
    assert (await tool.acall_send_tool("send", {"target": "evil", "message": "no"})) == "ok:evil:no"


async def test_unknown_intent_is_an_error() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["worker"])
    assert (await tool.asend("worker", "x", intent="nope")) == "[send] unknown intent 'nope'"
    assert run.calls == []
    assert (await tool.acall_send_tool("send", {"target": "worker", "message": "x", "intent": "nope"})) == (
        "[send] unknown intent 'nope'"
    )


async def test_empty_offer_list_hides_tools_send_still_sends() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run))
    assert not tool.offers("delegate_to_worker")
    assert not tool.offers("delegate_to_zzz")
    assert (await tool.asend("worker", "hello")) == "ok:worker:hello"
    assert (await tool.acall_send_tool("delegate_to_zzz", {"task": "x"})) == "ok:zzz:x"
    assert run.calls == [("worker", "hello", 0, "", ""), ("zzz", "x", 0, "", "")]


async def test_send_errors_and_claims() -> None:
    class Boom(LocalAgentComm):
        async def asend(self, *args, **kwargs):
            raise AgentCommError("down")

    class Missing:
        async def asend(self, *args, **kwargs):
            raise KeyError("gone")

    class Crash:
        async def asend(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert "via local-bus failed" in (await SendTool(comm=Boom(lambda *_: ""), allowed_peers=["w"]).asend("w", "t"))
    assert "not available on bus" in (
        await SendTool(
            comm=Missing(),
            allowed_peers=["w"],  # type: ignore[arg-type]
        ).asend("w", "t")
    )
    assert "failed: boom" in (
        await SendTool(
            comm=Crash(),
            allowed_peers=["w"],  # type: ignore[arg-type]
        ).asend("w", "t")
    )
    bare = SendTool(allowed_peers=["w"])
    with pytest.raises(AgentCommError, match="no communication plugin"):
        bare._resolve("w")
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["worker"])
    assert (await tool.asend("", "x")) == "[send] missing target"
    assert (await tool.asend("worker", "  ")) == "[send] missing message for 'worker'"
    assert (await tool.adelegate("worker", "job")) == "ok:worker:job"
    assert tool.claims("delegate_to_worker")
    assert not tool.claims("send")
    assert not tool.is_delegate_tool("send")
    assert not tool.claims("spawn_subagent")
    assert (await tool.acall_send_tool("web_search", {})) == "[send] not a send tool: 'web_search'"
    assert await tool.on_execute_tool("delegate_to_worker", {"task": "go"}) == "ok:worker:go"
    ctx = _ctx(agent_comm=LocalAgentComm(run), agent_comm_routes={})
    unbound = SendTool(allowed_peers=["worker"])
    assert (await unbound.acall("delegate_to_worker", {"task": "x"}, ctx=ctx)).startswith("ok:")


@pytest.mark.asyncio
async def test_asend_uses_same_prepare_including_routes() -> None:
    local = _RunTurn()
    remote_calls: list[tuple[str, str]] = []

    class Remote:
        plugin_id = "a2a"
        fabric_id = None
        endpoint = ""

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
    assert await tool.asend("worker", "job") == "ok:worker:job"
    assert await tool.asend("stranger", "job") == "ok:stranger:job"
    assert await tool.asend("", "x") == "[send] missing target"
    assert await tool.asend("worker", " ") == "[send] missing message for 'worker'"
    assert await tool.asend("worker", "x", intent="nope") == "[send] unknown intent 'nope'"
    assert await tool.acall_send_tool("nope", {}) == "[send] not a send tool: 'nope'"
    assert (await tool.acall("delegate_to_worker", {"task": "go"})).startswith("ok:")
    smuggled = await tool.acall_send_tool("delegate_to_worker", {"task": "go", "target": "evil"})
    assert "extra arguments" in smuggled

    class SyncOnly:
        async def asend(self, target, task, **_):
            return f"sync:{target}:{task}"

    sync_tool = SendTool(comm=SyncOnly(), allowed_peers=["w"])  # type: ignore[arg-type]
    assert await sync_tool.asend("w", "t") == "sync:w:t"

    class Boom:
        async def asend(self, *args, **kwargs):
            raise AgentCommError("down")

    class Missing:
        async def asend(self, *args, **kwargs):
            raise KeyError("gone")

    class Crash:
        async def asend(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert "failed" in await SendTool(comm=Boom(), allowed_peers=["w"]).asend("w", "t")  # type: ignore[arg-type]
    assert "not available" in await SendTool(
        comm=Missing(),
        allowed_peers=["w"],  # type: ignore[arg-type]
    ).asend("w", "t")
    assert "failed: boom" in await SendTool(
        comm=Crash(),
        allowed_peers=["w"],  # type: ignore[arg-type]
    ).asend("w", "t")
    ctx = _ctx(agent_comm=LocalAgentComm(local))
    unbound = SendTool(allowed_peers=["worker"])
    assert (await unbound.acall("delegate_to_worker", {"task": "z"}, ctx=ctx)).startswith("ok:")
    tool.reset_session()
    tool.set_routes({})
    collected = tool.on_collect_tools(ctx=_ctx(agent_id="moderator", manifest=_legacy_manifest()))
    assert [spec["name"] for spec in collected] == ["delegate_to_worker"]


async def test_bind_uses_this_call_not_the_first_session() -> None:
    first = _RunTurn()
    second = _RunTurn()
    tool = SendTool(allowed_peers=["worker"])
    ctx1 = _ctx(agent_comm=LocalAgentComm(first), agent_comm_routes={})
    ctx2 = _ctx(agent_comm=LocalAgentComm(second), agent_comm_routes={})
    assert await tool.acall("delegate_to_worker", {"task": "one"}, ctx=ctx1) == "ok:worker:one"
    assert await tool.acall("delegate_to_worker", {"task": "two"}, ctx=ctx2) == "ok:worker:two"
    assert first.calls == [("worker", "one", 0, "", "")]
    assert second.calls == [("worker", "two", 0, "", "")]


async def test_non_string_message_is_rejected() -> None:
    run = _RunTurn()
    tool = SendTool(comm=LocalAgentComm(run), allowed_peers=["worker"])
    assert (await tool.asend("worker", {"a": 1})) == "[send] message must be a string"  # type: ignore[arg-type]
    assert (await tool.acall_send_tool("delegate_to_worker", {"task": {"a": 1}})) == ("[send] message must be a string")
    assert run.calls == []


async def test_plugin_without_headers_or_context_is_called() -> None:
    class Legacy:
        async def asend(self, target_agent_id: str, task: str) -> str:
            return f"legacy:{target_agent_id}:{task}"

    tool = SendTool(comm=Legacy(), allowed_peers=["w"])  # type: ignore[arg-type]
    assert (await tool.asend("w", "t")) == "legacy:w:t"


async def test_type_error_inside_peer_is_not_a_tool_result() -> None:
    async def run_turn(*_args: object) -> str:
        return len(None)  # type: ignore[arg-type]

    tool = SendTool(comm=LocalAgentComm(run_turn), allowed_peers=["w"])
    with pytest.raises(TypeError):
        await tool.asend("w", "t")


async def test_headers_and_context_reach_a_plugin_that_takes_them() -> None:
    seen: list[dict[str, object]] = []

    class Modern:
        HONOURED_SEND_INTENTS = frozenset({"task", "inform", "ask"})

        async def asend(
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
    assert (await tool.asend("w", "t", intent="inform", headers={"traceparent": "00"})) == "ok"
    assert seen[0]["target"] == "w"
    assert seen[0]["task"] == "t"
    assert seen[0]["headers"] == {"traceparent": "00"}
    context = seen[0]["context"]
    assert isinstance(context, SendContext)
    assert context.intent == "inform"


async def test_same_fabric_send_uses_this_identity_not_the_target_handler() -> None:
    class Sender(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = None
        endpoint = ""

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            return f"from-sender:{target_agent_id}:{task}"

    class Target(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = None
        endpoint = ""

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            raise AssertionError("target handler must not send")

    tool = SendTool(
        comm=Sender(),  # type: ignore[arg-type]
        routes={"worker": AgentCommRoute("worker", "meshx", Target())},  # type: ignore[arg-type]
        allowed_peers=["worker"],
    )
    assert (await tool.asend("worker", "x")) == "from-sender:worker:x"


async def test_same_kind_on_different_endpoints_uses_the_peer_handler() -> None:
    class Sender(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = None
        endpoint = "http://127.0.0.1:46357"

        async def asend(self, *_: object, **__: object) -> str:
            raise AssertionError("must not use sender for another stack")

    class Peer(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = None
        endpoint = "http://127.0.0.1:46358"

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            return f"other-plane:{target_agent_id}:{task}"

    tool = SendTool(
        comm=Sender(),  # type: ignore[arg-type]
        routes={"worker": AgentCommRoute("worker", "meshx", Peer())},  # type: ignore[arg-type]
        allowed_peers=["worker"],
    )
    assert (await tool.asend("worker", "x")) == "other-plane:worker:x"


async def test_same_fabric_id_uses_caller_send() -> None:
    class Sender(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = 7

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            return f"from-sender:{target_agent_id}:{task}"

    class Target(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = 7

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            raise AssertionError("target handler must not send")

    tool = SendTool(
        comm=Sender(),  # type: ignore[arg-type]
        routes={"worker": AgentCommRoute("worker", "meshx", Target())},  # type: ignore[arg-type]
        allowed_peers=["worker"],
    )
    assert (await tool.asend("worker", "x")) == "from-sender:worker:x"


async def test_different_fabric_id_uses_the_peer_handler() -> None:
    class Sender(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = 1

        async def asend(self, *_: object, **__: object) -> str:
            raise AssertionError("must not use sender for another stack")

    class Peer(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = 2

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            return f"other-plane:{target_agent_id}:{task}"

    tool = SendTool(
        comm=Sender(),  # type: ignore[arg-type]
        routes={"worker": AgentCommRoute("worker", "meshx", Peer())},  # type: ignore[arg-type]
        allowed_peers=["worker"],
    )
    assert (await tool.asend("worker", "x")) == "other-plane:worker:x"


async def test_a2a_route_still_uses_the_peer_handler() -> None:
    class Sender(LinkIdentity):
        plugin_id = "meshx"
        fabric_id = None
        endpoint = ""

        async def asend(self, *_: object, **__: object) -> str:
            raise AssertionError("must not use sender for a2a")

    class Peer:
        plugin_id = "a2a"
        fabric_id = None
        endpoint = ""

        async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
            return f"a2a:{target_agent_id}:{task}"

    tool = SendTool(
        comm=Sender(),  # type: ignore[arg-type]
        routes={"worker": AgentCommRoute("worker", "a2a", Peer())},  # type: ignore[arg-type]
        allowed_peers=["worker"],
    )
    assert (await tool.asend("worker", "x")) == "a2a:worker:x"


async def test_session_comm_and_routes_replace_configured() -> None:
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
    assert (await tool.asend("worker", "x", ctx=ctx)) == "ok:worker:x"
    assert configured.calls == []
    assert session.calls == [("worker", "x", 0, "", "")]


def test_engine_tool_context_does_not_declare_the_comm_bus() -> None:
    from mas.runtime.boundary.engine_tools import EngineToolContext

    assert "agent_comm" not in EngineToolContext.__dict__
    assert "arun_turn" in EngineToolContext.__dict__


def test_send_tool_reads_send_tool_context() -> None:
    assert get_type_hints(SendTool.asend)["ctx"] == SendToolContext | None
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
