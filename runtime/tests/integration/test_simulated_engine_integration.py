#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Simulated engine integration."""

from mas.runtime.engine.simulated import SimMode, SimulatedEngine, simulated_next_step
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn


def test_simulated_next_step_alternates():
    assert simulated_next_step(SimMode.DEFAULT, 2) == "STOP"
    assert simulated_next_step(SimMode.DEFAULT, 1) == "TOOL_CALL"


async def test_simulated_engine_script_override():
    engine = SimulatedEngine(
        script={
            7: EngineIoReturn(
                correlation_id=7,
                response_kind="MODEL_TEXT",
                next_step="STOP",
                text="scripted",
            )
        }
    )
    out = await engine.ainvoke(InvokeEngineIo(correlation_id=7, op="LLM_CALL"))
    assert out.text == "scripted"


async def test_simulated_engine_tool_call_path():
    engine = SimulatedEngine()
    out = await engine.ainvoke(InvokeEngineIo(correlation_id=3, op="TOOL_CALL"))
    assert out.response_kind == "TOOL_RESULT"
