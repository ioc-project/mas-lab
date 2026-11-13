# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from mas.runtime.boundary.connection import (
    BindingUnavailable,
    ConnectionBinding,
    ConnectionTarget,
    Invocation,
)


def binding(effects, target="https://worker-a.test"):
    def factory(name, args, ctx):
        return Invocation(lambda **_: effects.append((target, args)))

    return ConnectionBinding("a2a", "http", ConnectionTarget(endpoint=target), factory)


def test_replacement_invalidates_pending_operation_with_same_transport():
    effects = []
    owner = binding(effects)
    pending = owner.prepare("delegate", {"id": 1})
    replacement = "https://worker-b.test"
    owner.replace(
        "a2a",
        "http",
        ConnectionTarget(endpoint=replacement),
        lambda *_: Invocation(lambda: effects.append(replacement)),
    )
    with pytest.raises(BindingUnavailable):
        pending.invoke()
    assert effects == []
    assert pending.connection.matches_target(ConnectionTarget(endpoint="https://worker-a.test"))
    assert not pending.connection.matches_target(ConnectionTarget(endpoint=replacement))


def test_dispatch_admission_pins_old_target_when_replacement_loses_race():
    entered, release = threading.Event(), threading.Event()
    effects = []

    def factory(*_):
        def invoke():
            entered.set()
            assert release.wait(5)
            effects.append("old")

        return Invocation(invoke)

    owner = ConnectionBinding("mcp", "stdio", ConnectionTarget(command=("old",)), factory)
    pending = owner.prepare("write", {})
    with ThreadPoolExecutor() as pool:
        future = pool.submit(pending.invoke)
        assert entered.wait(5)
        owner.replace(
            "mcp", "stdio", ConnectionTarget(command=("new",)), lambda *_: Invocation(lambda: effects.append("new"))
        )
        release.set()
        future.result(5)
    assert effects == ["old"]
    with pytest.raises(BindingUnavailable):
        pending.invoke()


@pytest.mark.asyncio
async def test_async_and_sync_admission_share_replacement_and_cleanup():
    effects = []
    owner = binding(effects)
    pending = owner.prepare("delegate", {})
    owner.close()
    with pytest.raises(BindingUnavailable):
        await pending.ainvoke()
    with pytest.raises(BindingUnavailable):
        pending.invoke()
    with pytest.raises(BindingUnavailable):
        owner.prepare("delegate", {})
    assert effects == []


def test_closed_operation_and_argument_mutation_do_not_dispatch_changes():
    effects = []
    owner = binding(effects)
    args = {"nested": [1]}
    pending = owner.prepare("delegate", args)
    args["nested"].append(2)
    pending.invoke()
    assert effects == [("https://worker-a.test", {"nested": [1]})]
    pending.close()
    with pytest.raises(BindingUnavailable):
        pending.invoke()
    assert "https://" not in repr(pending.connection)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("verdict", ["allow", "block", "replace", "error"])
def test_native_governance_and_dispatch_share_prepared_binding(monkeypatch, verdict, asynchronous):
    from mas.runtime.boundary.gov.plugin import GovernancePluginChain
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.engine.llm_live import LiveLlmEngine
    from mas.runtime.engine.manifest_tool_provider import ManifestToolProvider
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.kernel.coupling import GovDecision
    from mas.runtime.schema.ingress import EngineIoReturn

    effects = []
    owner = binding(effects)
    captured = []
    issued = []

    class Provider:
        origin = "external"
        kind = "test"
        provider_name = "records"
        tools_claim = ("write",)

        def has_tools(self):
            return True

        def list_tools(self, **kwargs):
            return [{"name": "write", "parameters": {"type": "object", "properties": {}}}]

        def call_tool(self, *args, **kwargs):
            raise AssertionError("prepared call was resolved a second time")

        def prepare_call(self, name, arguments, **kwargs):
            operation = owner.prepare(name, arguments)
            issued.append(operation)
            return operation

    class Policy:
        requires_connection_metadata = True

        def evaluate_egress(self, intent, **kwargs):
            if intent.op != "TOOL_CALL":
                return GovDecision.ALLOW, "test", "outside scope"
            captured.append(intent.connection)
            assert intent.connection.matches_target(ConnectionTarget(endpoint="https://worker-a.test"))
            if verdict == "error":
                raise RuntimeError("policy failed")
            if verdict == "replace":
                owner.replace(
                    "a2a",
                    "http",
                    ConnectionTarget(endpoint="https://worker-b.test"),
                    lambda *_: Invocation(lambda: effects.append("wrong")),
                )
            return (GovDecision.BLOCK if verdict == "block" else GovDecision.ALLOW, "test", "fixture")

    provider = ManifestToolProvider([Provider()])
    engine = LiveLlmEngine(model="scripted", tool_provider=provider)
    script = iter(["TOOL_CALL", "STOP"])

    def model_reply(io):
        return EngineIoReturn(
            correlation_id=io.correlation_id,
            response_kind="MODEL_TEXT",
            next_step=next(script, "STOP"),
            tool_name="write",
            tool_arguments={"value": 1},
        )

    monkeypatch.setattr(engine, "_llm_call", model_reply)

    async def amodel_reply(io):
        return model_reply(io)

    monkeypatch.setattr(engine, "_allm_call", amodel_reply)
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(egress_governance_plugin=GovernancePluginChain([Policy()])),
        engine=engine,
        enable_observability=False,
    )
    trace = asyncio.run(instance.arun_user_text("write once")) if asynchronous else instance.run_user_text("write once")
    assert captured
    assert len(effects) == (1 if verdict == "allow" else 0), [
        str(symbol) for step in trace.steps for symbol in step.egress
    ]
    for operation in issued:
        with pytest.raises(BindingUnavailable):
            operation.invoke()


def test_modified_intent_preserves_connection_and_arguments():
    from mas.runtime.boundary.gov.policy import EgressIntentView, apply_egress_modify
    from mas.runtime.schema.governance import GovernanceAction

    operation = binding([]).prepare("write", {})
    intent = EgressIntentView("TOOL_CALL", True, 1, "write", {"value": "kept"}, ("write",), operation.connection)
    modified = apply_egress_modify(intent, GovernanceAction.MODIFY)
    assert modified.connection is intent.connection
    assert modified.tool_arguments == intent.tool_arguments
    assert modified.offered_tools == intent.offered_tools
    assert not modified.destructive


def test_prepared_engine_rejects_argument_changes_and_closes_on_stop():
    from types import SimpleNamespace

    from mas.runtime.engine.llm_live import LiveLlmEngine

    operations = []
    owner = binding([])

    def prepare(name, args, **kwargs):
        operation = owner.prepare(name, args)
        operations.append(operation)
        return operation

    engine = LiveLlmEngine(model="scripted", tool_provider=SimpleNamespace(prepare_call=prepare))
    engine.prepare_tool_connection("write", {"value": 1}, 1)
    with pytest.raises(BindingUnavailable, match="arguments changed"):
        engine._prepared_for(1, "write", {"value": 2})
    # Repreparation closes the superseded handle, even for the same call ID.
    engine.prepare_tool_connection("write", {"value": 1}, 1)
    with pytest.raises(BindingUnavailable):
        operations[0].invoke()
    engine.stop()
    assert engine._prepared_connections == {}
    with pytest.raises(BindingUnavailable):
        operations[1].invoke()


def test_closed_binding_cannot_be_replaced_and_metadata_is_read_only():
    owner = binding([])
    operation = owner.prepare("write", {})
    with pytest.raises(AttributeError):
        operation.connection = None
    owner.close()
    with pytest.raises(BindingUnavailable):
        owner.replace("mcp", "stdio", ConnectionTarget(command=("other",)), lambda *_: Invocation(lambda: None))


@pytest.mark.parametrize("kernel_state", [False, True])
def test_preparation_preserves_native_provider_context_sealing(kernel_state):
    from types import SimpleNamespace

    from mas.runtime.engine.tool_workers import ToolCallContext
    from mas.runtime.registry.tool_provider_registry import ToolProviderRegistry

    captured = []
    owner = ConnectionBinding(
        "mcp",
        "stdio",
        ConnectionTarget(command=("records",)),
        lambda name, args, ctx: Invocation(lambda: ctx.user),
        binding_ref="mcp:records",
    )

    class Provider:
        origin = "external"
        provider_name = "records"
        tools_claim = ("write",)

        def list_tools(self, **kwargs):
            return [{"name": "write"}]

        def call_tool(self, *args, **kwargs):
            raise AssertionError("preparation must not execute")

        def prepare_call(self, name, args, *, ctx):
            captured.append(ctx)
            return owner.prepare(name, args, ctx=ctx)

    provider = Provider()
    provider.kernel_state = kernel_state
    registry = ToolProviderRegistry()
    registry.register_provider(provider)
    context = SimpleNamespace(user="original", session_id="session", agent_id="worker", turn_index=1, current_spec={})
    operation = registry.prepare_call("write", {}, ctx=context)
    assert isinstance(captured[0], ToolCallContext) is not kernel_state
    context.user = "changed"
    assert operation.invoke() == ("changed" if kernel_state else "original")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_prepared_business_tool_with_delegation_prefix_keeps_execution_scope_guard(asynchronous):
    from mas.runtime.engine.manifest_tool_provider import ManifestToolLoadError
    from mas.runtime.engine.tool_dispatch import aexecute_engine_tool, execute_engine_tool
    from mas.runtime.reliability.classes import ClassifiedFailure

    effects = []
    operation = binding(effects).prepare("delegate_to_records", {})

    class Delegation:
        def offers(self, name):
            return False

        def is_delegate_tool(self, name):
            return True

    class ScopedProvider:
        def assert_call_allowed(self, name, **kwargs):
            raise ManifestToolLoadError("tool is disabled")

    options = {"delegation": Delegation(), "tool_provider": ScopedProvider(), "prepared": operation}
    with pytest.raises(ClassifiedFailure, match="disabled"):
        if asynchronous:
            asyncio.run(aexecute_engine_tool("delegate_to_records", **options))
        else:
            execute_engine_tool("delegate_to_records", **options)
    assert effects == []
