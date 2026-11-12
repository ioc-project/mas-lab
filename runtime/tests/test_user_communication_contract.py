#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITLContract/UserIOContract: the default (registry-backed) implementations behave
exactly like the old inline fallback did, and a custom contract -- previously a dead
parameter nobody ever set -- is now actually invoked instead of the registry."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mas.library.standard.plugins.hitl.contracts import (
    RegistryHitlContract,
    RegistryUserIOContract,
)
from mas.runtime.boundary.hitl.registry import get_hitl_resolver_registry


@dataclass
class _FakeCtx:
    session_id: str
    agent_id: str = "finance-agent"
    correlation_id: int = 1


@pytest.fixture()
def empty_tool_tree(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# RegistryHitlContract / RegistryUserIOContract in isolation
# ---------------------------------------------------------------------------


def test_registry_hitl_contract_blocks_until_resolved():
    contract = RegistryHitlContract()
    registry = get_hitl_resolver_registry()

    def resolve_soon():
        for _ in range(50):
            if registry.has_pending("sess-contract-block", "agent-a"):
                break
            time.sleep(0.01)
        registry.resolve("sess-contract-block", "agent-a", 1, choice="approve", steering="go ahead")

    threading.Thread(target=resolve_soon, daemon=True).start()

    result = contract.request_approval(
        question="Approve?",
        session_id="sess-contract-block",
        requesting_user_id="",
        agent_id="agent-a",
        correlation_id=1,
        question_type="CONFIRM",
        choices=["approve", "reject"],
        context_data={},
    )
    assert result == {
        "choice": "approve",
        "steering": "go ahead",
        "question": "Approve?",
        "resolved": True,
    }


def test_registry_hitl_contract_raises_timeout_error():
    contract = RegistryHitlContract()
    with pytest.raises(TimeoutError):
        contract.request_approval(
            question="Approve?",
            session_id="sess-contract-timeout",
            requesting_user_id="",
            agent_id="agent-a",
            correlation_id=1,
            question_type="CONFIRM",
            choices=["approve", "reject"],
            context_data={},
            timeout=0.05,
        )


def test_registry_user_io_contract_registers_update():
    contract = RegistryUserIOContract()
    registry = get_hitl_resolver_registry()

    contract.send_progress_update(
        message="working on it",
        session_id="sess-userio",
        agent_id="agent-a",
        correlation_id=1,
        requesting_user_id="jordan",
        involved_agents=["agent-b"],
        metadata={"step": 1},
    )

    updates = registry.get_pending_user_updates_for_session("sess-userio")
    assert any(u.message == "working on it" for entries in updates.values() for u in entries)


# ---------------------------------------------------------------------------
# A custom contract is now actually invoked (previously a dead parameter)
# ---------------------------------------------------------------------------


class _RecordingHitlContract:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request_approval(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"choice": "custom-approved"}


class _RecordingUserIOContract:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_progress_update(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return {"delivered": True}


def test_custom_hitl_contract_is_used_instead_of_registry(empty_tool_tree: Path):
    from mas.library.standard.plugins.system_tools.request_human_input import RequestHumanInputTool
    from mas.library.standard.plugins.system_tools.wrappers import SystemToolHitlWrapper

    contract = _RecordingHitlContract()
    wrapper = SystemToolHitlWrapper(RequestHumanInputTool(), hitl_contract=contract)
    ctx = _FakeCtx(session_id="sess-custom-hitl")

    result = wrapper.on_execute_tool(
        "request_human_input",
        {"question": "Approve?", "question_type": "CONFIRM", "choices": ["approve", "reject"]},
        ctx=ctx,
    )

    assert result["choice"] == "custom-approved"
    assert len(contract.calls) == 1
    assert contract.calls[0]["question"] == "Approve?"
    # The registry must never have been touched for this call.
    registry = get_hitl_resolver_registry()
    assert not registry.has_pending("sess-custom-hitl", "finance-agent")


def test_custom_user_io_contract_is_used_instead_of_registry(empty_tool_tree: Path):
    from mas.library.standard.plugins.system_tools.inform_user import InformUserTool
    from mas.library.standard.plugins.system_tools.wrappers import SystemToolUserUpdateWrapper

    contract = _RecordingUserIOContract()
    wrapper = SystemToolUserUpdateWrapper(InformUserTool(), user_io_contract=contract)
    ctx = _FakeCtx(session_id="sess-custom-userio")

    result = wrapper.on_execute_tool(
        "inform_user",
        {"message": "On it", "user_name": "jordan", "involved_agents": []},
        ctx=ctx,
    )

    assert result["receipt"] == {"delivered": True}
    assert len(contract.calls) == 1
    assert contract.calls[0]["message"] == "On it"
    registry = get_hitl_resolver_registry()
    assert registry.get_pending_user_updates_for_session("sess-custom-userio") == {}


def test_default_contract_is_registry_backed(empty_tool_tree: Path):
    from mas.library.standard.plugins.system_tools.inform_user import InformUserTool
    from mas.library.standard.plugins.system_tools.request_human_input import RequestHumanInputTool
    from mas.library.standard.plugins.system_tools.wrappers import (
        SystemToolHitlWrapper,
        SystemToolUserUpdateWrapper,
    )

    hitl_wrapper = SystemToolHitlWrapper(RequestHumanInputTool())
    assert isinstance(hitl_wrapper._hitl_contract, RegistryHitlContract)

    io_wrapper = SystemToolUserUpdateWrapper(InformUserTool())
    assert isinstance(io_wrapper._user_io_contract, RegistryUserIOContract)
