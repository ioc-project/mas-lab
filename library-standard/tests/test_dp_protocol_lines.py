#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Design-pattern protocol lines reach every LLM call for the current phase, once.

A whole turn runs against a scripted engine that records the system prompt the
assembler builds for each LLM call.
"""

from __future__ import annotations

import json

from mas.library.standard.plugins.design_patterns.plan_execute import PlanExecutePlugin
from mas.library.standard.plugins.design_patterns.tree_of_thoughts import TreeOfThoughtsPlugin
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

PLAN_HEADER = "=== PLAN-AND-EXECUTE PROTOCOL ==="
SYNTH_HEADER = "=== PLAN-AND-EXECUTE: FINAL SYNTHESIS ==="
TOT_GENERATE = "=== TREE-OF-THOUGHTS PROTOCOL ==="
TOT_FINALIZE = "=== TREE-OF-THOUGHTS: SYNTHESIS ==="


class _Engine:
    """Plan → one tool call → synthesis; records the system prompt of each LLM call."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.instance: RuntimeInstance | None = None
        self.systems: list[str] = []

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if io.op == "TOOL_CALL":
            return EngineIoReturn(
                correlation_id=io.correlation_id, response_kind="TOOL_RESULT", next_step="STOP", text="42"
            )
        ctx = self.instance.driver.ctx
        messages = assemble_llm_messages(ctx, manifest={})
        self.systems.append(next((m["content"] for m in messages if m["role"] == "system"), ""))
        return EngineIoReturn(
            correlation_id=io.correlation_id, response_kind="MODEL_TEXT", next_step="STOP", text=self.answers.pop(0)
        )

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        return self.invoke(io)


def _instance(plugin: object, plugin_id: str, engine: _Engine) -> RuntimeInstance:
    instance = RuntimeInstance.from_parts(
        config=KernelConfig(), engine=engine, enable_observability=False, enable_coordination=False
    )
    instance.kernel.plugin = plugin
    instance.driver.ctx.pattern_plugin_id = plugin_id
    engine.instance = instance
    return instance


PLAN = json.dumps({"plan": [{"tool": "lookup", "arguments": {}}]})


def test_plan_execute_synthesis_call_sees_the_synthesis_lines() -> None:
    engine = _Engine([PLAN, "The answer is 42."])
    instance = _instance(PlanExecutePlugin(), "plan_execute@v1", engine)
    trace = instance.run_user_text("What is the answer?")

    assert trace.client_responses[-1].content == "The answer is 42."
    plan_call, synth_call = engine.systems
    assert PLAN_HEADER in plan_call and SYNTH_HEADER not in plan_call
    assert SYNTH_HEADER in synth_call and PLAN_HEADER not in synth_call


def test_tree_of_thoughts_final_call_sees_the_finalize_lines() -> None:
    thoughts = json.dumps({"thoughts": [{"content": "a", "score": 0.9}]})
    engine = _Engine([thoughts, "Final answer."])
    instance = _instance(TreeOfThoughtsPlugin(), "tree_of_thoughts@v1", engine)
    instance.run_user_text("Think about it.")

    first, last = engine.systems[0], engine.systems[-1]
    assert TOT_GENERATE in first and TOT_FINALIZE not in first
    assert TOT_FINALIZE in last and TOT_GENERATE not in last


def test_protocol_lines_do_not_pile_up_across_turns() -> None:
    engine = _Engine([PLAN, "one", PLAN, "two", PLAN, "three"])
    instance = _instance(PlanExecutePlugin(), "plan_execute@v1", engine)
    for text in ("first", "second", "third"):
        instance.run_user_text(text)

    assert len(engine.systems) == 6
    for system in engine.systems:
        assert system.count(PLAN_HEADER) + system.count(SYNTH_HEADER) == 1
    assert PLAN_HEADER not in "".join(instance.driver.ctx.injected_context)


def test_memory_seeds_appear_once_per_call() -> None:
    engine = _Engine([PLAN, "one", PLAN, "two"])
    instance = _instance(PlanExecutePlugin(), "plan_execute@v1", engine)
    instance.driver.ctx.memory_seeds.append(("customer", "Acme, gold tier"))
    instance.run_user_text("first")
    instance.run_user_text("second")

    for system in engine.systems:
        assert system.count("[memory:customer] Acme, gold tier") == 1
