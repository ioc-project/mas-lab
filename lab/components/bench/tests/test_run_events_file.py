#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A run whose agent manifest names a ``native`` observability entry still writes the run's events file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scaling_support import (
    isolate_lab_env,
    lab_ports,
    prepare_lab,
    results_rows,
    run_scaling_lab,
    stop_spawned_listeners,
)


@pytest.fixture
def scaling_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    isolate_lab_env(monkeypatch, tmp_path)
    lab = prepare_lab(tmp_path)
    yield lab
    stop_spawned_listeners(*lab_ports(lab))


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_on", [False, True])
async def test_native_entry_in_the_agent_manifest_keeps_the_run_events_file(
    scaling_lab: Path, tmp_path: Path, transport_on: bool
):
    agent = scaling_lab / "agent.yaml"
    agent.write_text(
        agent.read_text(encoding="utf-8")
        + "  observability:\n    - native:\n        transport:\n          defaults:\n"
        + f"            enabled: {str(transport_on).lower()}\n",
        encoding="utf-8",
    )
    out = await run_scaling_lab(scaling_lab, tmp_path, limit=1)
    events = Path(results_rows(out)[0]["trace_path"])
    assert events.is_file(), "the run has its own events file"
    kinds = [json.loads(line).get("kind", "") for line in events.read_text(encoding="utf-8").splitlines() if line]
    assert "llm_call_end" in kinds
    assert not (scaling_lab / "traces" / "events.jsonl").exists(), "no stray file next to the manifest"
