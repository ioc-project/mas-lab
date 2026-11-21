#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The default agent runner (the kernel main loop) is byte-identical to the path before runners existed.

``golden/default_runner.json`` was captured from the hosted-agent handler before the runner seam was
added: two scripted turns on one A2A conversation, the handler results and every event the agent's
observability recorded. This test reruns the same script and compares byte for byte (call ids are
random UUIDs, so they are replaced by a fixed token on both sides). To regenerate the file on
purpose, run this module: ``python ctl/tests/test_default_runner_parity.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mas.ctl.session.exposure import make_runtime_handler
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig

GOLDEN = Path(__file__).parent / "golden" / "default_runner.json"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _build() -> RuntimeInstance:
    engine = SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="ok")
    instance = RuntimeInstance.from_parts(engine=engine, config=KernelConfig())
    instance.capture_session_baseline()
    instance.conversation_factory = _build
    return instance


def scripted_run() -> str:
    """Two turns on one conversation; the results and the recorded events as canonical JSON text."""
    handler = make_runtime_handler(_build())
    results = [
        handler("hello", turn_id="t1", session_id="s1", parent_call_id="p1", upstream_correlation_id=7),
        handler("again", turn_id="t2", session_id="s1"),
    ]
    live = handler.manager.get("s1").controller.instance
    events = [event.model_dump(mode="json") for event in live.driver.observability.events]
    text = json.dumps({"results": results, "events": events}, indent=1, sort_keys=True, default=str)
    return _UUID.sub("UUID", text) + "\n"


def test_default_runner_matches_the_recorded_run() -> None:
    assert scripted_run() == GOLDEN.read_text(encoding="utf-8")


if __name__ == "__main__":
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(scripted_run(), encoding="utf-8")
