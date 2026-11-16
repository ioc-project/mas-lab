#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A run's native records: the thinking interval of an LLM call, and owners.

``record_llm_thinking`` reaches the native trace as ``thinking_start`` /
``thinking_end`` of the LLM call with that correlation id. Every record of a
run that is not itself a call names a call of the run as its owner (``call_id``
or ``parent_call_id``), never a contract-call wrapper that is no node.
"""

from __future__ import annotations

import json
from pathlib import Path

from mas.library.standard.lib.observability.emit import JsonlFileEmitter
from mas.library.standard.lib.observability.native.transform import NativeObservabilityTransform, TransformContext
from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
from mas.runtime.boundary.obs.plugins import ObsPluginSet, attach_observability_plugin_set
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.schema.ingress import EngineIoReturn

_CALL_STARTS = {
    "mas_call_start",
    "execution_start",
    "llm_call_start",
    "tool_call_start",
    "memory_call_start",
    "processing_call_start",
    "thinking_start",
}


class _ThinkingEngine(SimulatedEngine):
    """Reports a reasoning interval while it answers, as a live engine does."""

    observability = None

    async def ainvoke(self, io):  # type: ignore[override]
        if io.op == "LLM_CALL" and self.observability is not None:
            self.observability.record_llm_thinking(correlation_id=io.correlation_id, boundary="start", at=100.0)
            self.observability.record_llm_thinking(
                correlation_id=io.correlation_id, boundary="end", at=101.5, text="weigh it"
            )
        return await super().ainvoke(io)


def _run(tmp_path: Path) -> list[dict]:
    events_path = tmp_path / "events.jsonl"
    engine = _ThinkingEngine(
        model="openai/o4-mini",
        llm_messages=[{"role": "user", "content": "hi"}],
        script={
            1: EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP", text="ok"),
        },
    )
    inst = RuntimeInstance.from_parts(engine=engine)
    engine.observability = inst.driver.observability
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[JsonlFileEmitter(events_path)],
        context=TransformContext(agent_id="planner", run_id="run-thinking"),
    )
    plugin_set = ObsPluginSet(plugins=[plugin])
    attach_observability_plugin_set(plugin_set, inst, agent_id="planner")
    inst.capture_session_baseline()
    inst.run_user_text("hi")
    plugin_set.close()
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


def test_record_llm_thinking_becomes_the_llm_calls_thinking_interval(tmp_path: Path) -> None:
    records = _run(tmp_path)
    (llm,) = [r for r in records if r["kind"] == "llm_call_start"]
    (start,) = [r for r in records if r["kind"] == "thinking_start"]
    (end,) = [r for r in records if r["kind"] == "thinking_end"]
    assert start["parent_call_id"] == end["parent_call_id"] == llm["call_id"]
    assert start["call_id"] == end["call_id"] == f"{llm['call_id']}#think"
    assert (start["timestamp"], end["timestamp"]) == (100.0, 101.5)
    assert start["state_in"] == llm["state_in"]
    assert end["state_out"] == f"{llm['call_id']}#think#0"
    assert end["state_out_content"] == "weigh it"


def test_every_record_of_a_run_names_a_call_of_the_run(tmp_path: Path) -> None:
    records = _run(tmp_path)
    calls = {r["call_id"] for r in records if r["kind"] in _CALL_STARTS}
    unowned = [
        r["kind"]
        for r in records
        if r["kind"] not in _CALL_STARTS
        and not r["kind"].endswith("_end")
        and r.get("call_id") not in calls
        and r.get("parent_call_id") not in calls
    ]
    assert not unowned, unowned
