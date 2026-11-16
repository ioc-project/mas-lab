from __future__ import annotations

import asyncio

import pytest
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator
from mas.runtime.boundary.agentcomm import (
    AgentCommRoute,
    UnknownDelegationPeerError,
    build_agent_comm_routes,
)


class RemoteAgentComm:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def asend(self, target_agent_id: str, task: str, **_: object) -> str:
        self.calls.append((target_agent_id, task))
        return "remote result"


async def test_explicit_peer_route_overrides_materialized_local_agent() -> None:
    local = LocalAgentComm(lambda *_: asyncio.sleep(0, "local result"))
    remote = RemoteAgentComm()
    routes = build_agent_comm_routes(
        ["schedule_agent"],
        local,
        {
            "schedule_agent": AgentCommRoute(
                agent_id="schedule_agent",
                kind="a2a",
                handler=remote,
            )
        },
        delegated_names={"schedule_agent"},
    )

    delegator = LlmDelegator(comm=local, routes=routes, allowed_peers=["schedule_agent"])

    assert (await delegator.adelegate("schedule_agent", "find a train")) == "remote result"
    assert remote.calls == [("schedule_agent", "find a train")]


def test_unconfigured_delegation_target_fails_when_routes_are_built() -> None:
    with pytest.raises(UnknownDelegationPeerError, match="external_agent"):
        build_agent_comm_routes(
            ["local_agent"],
            LocalAgentComm(lambda *_: asyncio.sleep(0, "local result")),
            {},
            delegated_names={"external_agent"},
        )
