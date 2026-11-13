#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Engine tool dispatch — delegation contract routing."""

import asyncio
import threading
import time

import pytest
from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator
from mas.runtime.engine.manifest_tool_provider import build_manifest_tool_provider
from mas.runtime.engine.tool_dispatch import aexecute_engine_tool, execute_engine_tool


def test_execute_engine_tool_routes_delegate_tools():
    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: f"delegated:{aid}:{task}"),
        allowed_peers=["db"],
    )
    out = execute_engine_tool(
        "delegate_to_db",
        delegation=delegator,
        arguments={"task": "check connections"},
    )
    assert out == "delegated:db:check connections"


def test_execute_engine_tool_forwards_caller_call_id_to_delegation():
    """caller_call_id (this TOOL_CALL's own resolved call_id, attached by
    the driver) must reach the DelegationContract unchanged, so a
    delegate's own execution_start.parent_call_id is a real native value."""
    seen: list[str] = []
    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: seen.append(ccid) or f"delegated:{aid}"),
        allowed_peers=["db"],
    )
    execute_engine_tool(
        "delegate_to_db",
        delegation=delegator,
        arguments={"task": "check connections"},
        caller_call_id="tool-call-xyz",
    )
    assert seen == ["tool-call-xyz"]


def test_execute_engine_tool_leaves_user_tool_named_send_to_provider():
    seen: list[str] = []

    class Provider:
        def call_tool(self, tool, arguments, **kwargs):
            seen.append(tool)
            return "from-user-tool"

    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: f"delegated:{aid}"),
        allowed_peers=["db"],
    )
    out = execute_engine_tool(
        "send",
        delegation=delegator,
        arguments={"target": "db", "message": "hi"},
        tool_provider=Provider(),
    )
    assert out == "from-user-tool"
    assert seen == ["send"]
    assert not delegator.is_delegate_tool("send")


def test_execute_engine_tool_leaves_unlisted_delegate_name_to_provider():
    seen: list[str] = []

    class Provider:
        def call_tool(self, tool, arguments, **kwargs):
            seen.append(tool)
            return "from-user-tool"

    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: f"delegated:{aid}"),
        allowed_peers=["db"],
    )
    out = execute_engine_tool(
        "delegate_to_stranger",
        delegation=delegator,
        arguments={"task": "hi"},
        tool_provider=Provider(),
    )
    assert out == "from-user-tool"
    assert seen == ["delegate_to_stranger"]
    assert delegator.send("stranger", "hi") == "delegated:stranger"


def test_execute_engine_tool_with_no_peer_list_does_not_claim_delegate_names():
    seen: list[str] = []

    class Provider:
        def call_tool(self, tool, arguments, **kwargs):
            seen.append(tool)
            return "from-user-tool"

    delegator = LlmDelegator(
        comm=LocalAgentComm(lambda aid, task, cid, ccid, ctx_id: f"delegated:{aid}"),
    )
    out = execute_engine_tool(
        "delegate_to_db",
        delegation=delegator,
        arguments={"task": "hi"},
        tool_provider=Provider(),
    )
    assert out == "from-user-tool"
    assert seen == ["delegate_to_db"]


def test_execute_engine_tool_runs_async_on_execute_tool_to_completion():
    from mas.library.standard.plugins.tools.local import LocalToolProvider

    class _AsyncOnExecute:
        def on_collect_tools(self, **_):
            return [{"name": "echo"}]

        async def on_execute_tool(self, name, args, **_):
            return {"one-method": name, **args}

    provider = LocalToolProvider()
    provider._add_instance(_AsyncOnExecute(), {"name": "echo"})
    out = execute_engine_tool("echo", tool_provider=provider, arguments={"q": 1})
    assert '"one-method": "echo"' in out
    assert '"q": 1' in out


@pytest.mark.asyncio
async def test_execute_engine_tool_from_a_running_loop_tells_you_to_use_aexecute():
    from mas.library.standard.plugins.tools.local import LocalToolProvider

    class _AsyncOnExecute:
        def on_collect_tools(self, **_):
            return [{"name": "echo"}]

        async def on_execute_tool(self, name, args, **_):
            return {"one-method": name, **args}

    provider = LocalToolProvider()
    provider._add_instance(_AsyncOnExecute(), {"name": "echo"})
    with pytest.raises(Exception, match="use acall_tool"):
        execute_engine_tool("echo", tool_provider=provider, arguments={"q": 1})


@pytest.mark.asyncio
async def test_aexecute_engine_tool_awaits_async_on_execute_tool():
    from mas.library.standard.plugins.tools.local import LocalToolProvider

    class _AsyncOnExecute:
        def on_collect_tools(self, **_):
            return [{"name": "echo"}]

        async def on_execute_tool(self, name, args, **_):
            return {"one-method": name, **args}

    provider = LocalToolProvider()
    provider._add_instance(_AsyncOnExecute(), {"name": "echo"})
    out = await aexecute_engine_tool("echo", tool_provider=provider, arguments={"q": 1})
    assert '"one-method": "echo"' in out


def test_execute_engine_tool_routes_ordered_contract_before_manifest_provider():
    calls = []

    class EngineTool:
        def claims(self, tool_name):
            return tool_name == "create_subagent"

        def call(self, tool_name, arguments, **kwargs):
            calls.append((tool_name, arguments, kwargs["caller_call_id"]))
            return "spawned"

    result = execute_engine_tool(
        "create_subagent",
        engine_contracts=[EngineTool()],
        arguments={"template": "worker", "task": "review"},
        caller_call_id="parent-call",
    )

    assert result == "spawned"
    assert calls == [("create_subagent", {"template": "worker", "task": "review"}, "parent-call")]


def test_execute_engine_tool_uses_manifest_provider(tmp_path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calc.py").write_text(
        """
class CalcTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name != "calculator":
            return None
        return {"result": 65536}
""",
        encoding="utf-8",
    )
    import yaml

    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {
                    "description": "calc",
                    "impl": {"module_path": "./calc.py", "class_name": "CalcTool"},
                },
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        tmp_path,
    )
    out = execute_engine_tool(
        "calculator",
        arguments={"expression": "2**16"},
        tool_provider=provider,
    )
    assert "65536" in out


def test_unknown_tool_raises(tmp_path):
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "calc.py").write_text(
        """
class CalcTool:
    def on_collect_tools(self, **_):
        return [{"name": "calculator", "description": "calc", "parameters": {"type": "object", "properties": {}}}]
    def on_execute_tool(self, name, args, **_):
        if name != "calculator":
            return None
        return {"ok": True}
""",
        encoding="utf-8",
    )
    import yaml

    (tool_dir / "calculator.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "calculator"},
                "spec": {"impl": {"module_path": "./calc.py", "class_name": "CalcTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider(
        [{"ref": "tools/calculator.tool.yaml"}],
        tmp_path,
    )
    from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

    with pytest.raises(ClassifiedFailure, match="not found") as caught:
        execute_engine_tool("missing", tool_provider=provider)
    assert caught.value.failure_class is FailureClass.APPLICATION
    assert caught.value.code == "TOOL_UNKNOWN"


class _SleepProvider:
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def call_tool(self, tool, arguments, **kwargs):  # noqa: ANN001, ARG002
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay)
            return f"slept-{tool}"
        finally:
            with self._lock:
                self.in_flight -= 1


class _AsyncSleepProvider:
    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    def call_tool(self, tool, arguments, **kwargs):  # noqa: ANN001, ARG002
        raise AssertionError("sync call_tool must not run when acall_tool is present")

    async def acall_tool(self, tool, arguments, **kwargs):  # noqa: ANN001, ARG002
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return f"async-{tool}"
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_aexecute_engine_tool_overlaps_sync_plugin_tools():
    provider = _SleepProvider()
    started = time.monotonic()
    left, right = await asyncio.gather(
        aexecute_engine_tool("alpha", tool_provider=provider),
        aexecute_engine_tool("beta", tool_provider=provider),
    )
    elapsed = time.monotonic() - started
    assert {left, right} == {"slept-alpha", "slept-beta"}
    assert provider.max_in_flight == 2
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_aexecute_engine_tool_prefers_provider_acall_tool():
    provider = _AsyncSleepProvider()
    started = time.monotonic()
    left, right = await asyncio.gather(
        aexecute_engine_tool("alpha", tool_provider=provider),
        aexecute_engine_tool("beta", tool_provider=provider),
    )
    elapsed = time.monotonic() - started
    assert {left, right} == {"async-alpha", "async-beta"}
    assert provider.max_in_flight == 2
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_manifest_plugin_tools_overlap_through_real_provider(tmp_path):
    """Sync ``on_execute_tool`` plugins from a manifest overlap on the async path."""
    import yaml

    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    (tool_dir / "slow.py").write_text(
        """
import time

class SlowTool:
    def on_collect_tools(self, **_):
        return [
            {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}
            for n in ("slow_a", "slow_b", "slow_c")
        ]

    def on_execute_tool(self, name, args, **_):
        time.sleep(0.3)
        return {"done": name}
""",
        encoding="utf-8",
    )
    (tool_dir / "slow.tool.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Tool",
                "metadata": {"name": "slow_a"},
                "spec": {"impl": {"module_path": "./slow.py", "class_name": "SlowTool"}},
            }
        ),
        encoding="utf-8",
    )
    provider = build_manifest_tool_provider([{"ref": "tools/slow.tool.yaml"}], tmp_path)
    started = time.monotonic()
    out = await asyncio.gather(
        *(aexecute_engine_tool(name, tool_provider=provider) for name in ("slow_a", "slow_b", "slow_c"))
    )
    elapsed = time.monotonic() - started
    assert [("done" in o) for o in out] == [True, True, True]
    assert elapsed < 0.7, f"three 0.3s tools ran serially: {elapsed:.2f}s"


class _CtxProbe:
    """Sync provider that records the ctx it was handed and tries to write to it."""

    def __init__(self, kernel_state: bool = False) -> None:
        self.kernel_state = kernel_state
        self.seen: list[object] = []
        self.write_error: BaseException | None = None

    def call_tool(self, tool, arguments, *, ctx=None, **kwargs):  # noqa: ANN001, ARG002
        self.seen.append(ctx)
        try:
            ctx.skill_state = "mutated"
        except BaseException as exc:  # noqa: BLE001
            self.write_error = exc
        return "ok"


def _live_ctx():
    from types import SimpleNamespace

    return SimpleNamespace(session_id="s1", agent_id="a1", user="u1", turn_index=3, skill_state="live")


@pytest.mark.asyncio
@pytest.mark.parametrize("worker", ["thread", "inline"])
async def test_a_tool_sees_the_same_sealed_ctx_wherever_it_runs(worker):
    from mas.runtime.engine.tool_workers import ToolCallContext, ToolPlacement

    live = _live_ctx()
    provider = _CtxProbe()
    await aexecute_engine_tool("t", tool_provider=provider, ctx=live, placement=ToolPlacement(worker=worker))
    seen = provider.seen[0]
    assert isinstance(seen, ToolCallContext) and seen is not live
    assert (seen.session_id, seen.agent_id, seen.user, seen.turn_index) == ("s1", "a1", "u1", 3)
    assert not hasattr(seen, "skill_state")
    assert provider.write_error is not None  # frozen: a tool cannot write run state
    assert live.skill_state == "live"


def test_the_sync_path_seals_the_ctx_too():
    live = _live_ctx()
    provider = _CtxProbe()
    execute_engine_tool("t", tool_provider=provider, ctx=live)
    from mas.runtime.engine.tool_workers import ToolCallContext

    assert isinstance(provider.seen[0], ToolCallContext)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker", ["thread", "inline"])
async def test_a_kernel_state_tool_gets_the_live_ctx_and_stays_in_the_kernel(worker):
    from mas.runtime.engine.tool_workers import ToolPlacement

    live = _live_ctx()
    provider = _CtxProbe(kernel_state=True)
    await aexecute_engine_tool("t", tool_provider=provider, ctx=live, placement=ToolPlacement(worker=worker))
    assert provider.seen[0] is live


@pytest.mark.asyncio
async def test_placement_changes_timing_only_never_results_or_order():
    from mas.runtime.engine.tool_workers import ToolPlacement

    async def _run(worker):
        provider = _SleepProvider(0.05)
        started = time.monotonic()
        out = await asyncio.gather(
            *(
                aexecute_engine_tool(f"t{i}", tool_provider=provider, placement=ToolPlacement(worker=worker))
                for i in range(4)
            )
        )
        return out, provider.max_in_flight, time.monotonic() - started

    inline_out, inline_peak, inline_time = await _run("inline")
    thread_out, thread_peak, thread_time = await _run("thread")
    assert inline_out == thread_out == [f"slept-t{i}" for i in range(4)]
    assert inline_peak == 1 and thread_peak == 4
    assert thread_time < inline_time


@pytest.mark.asyncio
async def test_tool_concurrency_is_the_only_cap_and_defaults_to_none():
    from mas.runtime.engine.tool_workers import ToolPlacement

    class _Rendezvous:
        def __init__(self, n: int) -> None:
            self._barrier = threading.Barrier(n, timeout=10)
            self.max_in_flight = 0
            self._lock = threading.Lock()
            self.in_flight = 0

        def call_tool(self, tool, arguments, **kwargs):  # noqa: ANN001, ARG002
            with self._lock:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self._barrier.wait()
            with self._lock:
                self.in_flight -= 1
            return f"ok-{tool}"

    provider = _Rendezvous(48)
    await asyncio.gather(
        *(
            aexecute_engine_tool(f"t{i}", tool_provider=provider, placement=ToolPlacement(worker="thread"))
            for i in range(48)
        )
    )
    assert provider.max_in_flight == 48  # no hidden pool limit, whatever the CPU count

    capped = _SleepProvider(0.05)
    limited = ToolPlacement(worker="thread", concurrency=3)
    await asyncio.gather(*(aexecute_engine_tool(f"t{i}", tool_provider=capped, placement=limited) for i in range(12)))
    assert capped.max_in_flight == 3


def test_an_unknown_tool_worker_is_rejected_when_the_placement_is_built():
    from mas.runtime.engine.tool_workers import ToolPlacement

    with pytest.raises(ValueError, match="unknown tool_worker"):
        ToolPlacement(worker="process")
    with pytest.raises(ValueError, match="tool_concurrency"):
        ToolPlacement(concurrency=0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("worker", "level"), [("inline", "WARNING"), ("thread", "INFO")])
async def test_a_sync_tool_is_flagged_once_and_louder_when_it_blocks_the_loop(worker, level, caplog):
    import logging

    from mas.runtime.engine import tool_workers
    from mas.runtime.engine.tool_workers import ToolPlacement, arun_sync_tool

    class FlaggedSyncTool:
        def run(self, *, ctx=None):
            return "ok"

    tool_workers._FLAGGED_SYNC.discard(FlaggedSyncTool)
    owner = FlaggedSyncTool()
    token = tool_workers.bind_placement(ToolPlacement(worker=worker))
    try:
        with caplog.at_level(logging.INFO, logger=tool_workers.logger.name):
            for _ in range(3):
                await arun_sync_tool(owner.run, owner=owner, ctx=_live_ctx())
    finally:
        tool_workers.unbind_placement(token)
    flagged = [r for r in caplog.records if "FlaggedSyncTool" in r.getMessage()]
    assert len(flagged) == 1
    assert flagged[0].levelname == level


def test_run_awaitable_uses_one_loop_for_every_sync_call() -> None:
    import asyncio
    import contextvars

    from mas.runtime.engine.tool_workers import run_awaitable

    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")

    async def where() -> tuple[int, str]:
        return id(asyncio.get_running_loop()), marker.get()

    marker.set("caller")
    first = run_awaitable(where())
    second = run_awaitable(where())
    assert first[0] == second[0]
    assert first[1] == "caller"


def test_run_awaitable_raises_what_the_awaitable_raises() -> None:
    import pytest
    from mas.runtime.engine.tool_workers import run_awaitable

    async def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_awaitable(boom())
