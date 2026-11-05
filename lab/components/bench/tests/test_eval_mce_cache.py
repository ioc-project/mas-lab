#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``eval_mce`` reuses ``metrics.json`` only when its inputs are unchanged.

``overwrite: false`` used to mean "skip if the file exists", so a changed
trace, metric list or judge model silently kept stale scores. The step now
stamps ``inputs_digest`` and reuses a file only when it matches (legacy files
are adopted once; a failed scoring is never reused).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from mas.library.lab.steps.eval import mce as mce_mod


@pytest.fixture
def harness(tmp_path, monkeypatch):
    run = tmp_path / "scn" / "item0" / "r0"
    (run / "traces").mkdir(parents=True)
    events = run / "traces" / "events.jsonl"
    events.write_text('{"kind":"execution_end","timestamp":1}\n', encoding="utf-8")
    calls = {"judge": 0, "model": "judge-a"}

    async def fake_compute(**kwargs):
        calls["judge"] += 1
        return {}

    monkeypatch.setattr(mce_mod, "_compute_mixed_metrics", fake_compute)
    monkeypatch.setattr(mce_mod, "_install_judge", lambda c, x: (calls["model"], "test"))
    monkeypatch.setattr(mce_mod, "_resolve_judge", lambda c, x: SimpleNamespace(model=calls["model"], source="test"))

    def run_step(**overrides):
        config = {
            "run_dir": str(run),
            "events_path": str(events),
            "metrics": ["goal_success_rate"],
            "scenario": "scn",
            "test": "item0",
            "validate": False,
        }
        config.update(overrides)
        step = mce_mod.EvalMceStep(name="eval-mce", config=config)
        ctx = SimpleNamespace(scope_context=None, template_vars={}, output_dir=tmp_path)
        return asyncio.run(step.execute(ctx))

    return SimpleNamespace(run=run, events=events, calls=calls, run_step=run_step)


def _doc(h) -> dict:
    return json.loads((h.run / "metrics.json").read_text(encoding="utf-8"))


def test_first_run_scores_and_stamps_digest(harness):
    out = harness.run_step()
    assert harness.calls["judge"] == 1
    assert out.data["computed"] == 1
    assert len(_doc(harness)["inputs_digest"]) == 64


def test_unchanged_inputs_do_not_call_the_judge(harness):
    harness.run_step()
    out = harness.run_step()
    assert harness.calls["judge"] == 1
    assert out.metadata["skipped"] is True
    assert out.metadata["reuse"] == "inputs unchanged"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda h: h.events.write_text("{}\n", encoding="utf-8"), id="events"),
        pytest.param(lambda h: h.calls.__setitem__("model", "judge-b"), id="judge-model"),
        pytest.param(lambda h: (h.run / "kg.json").write_text("{}", encoding="utf-8"), id="side-file"),
    ],
)
def test_changed_inputs_rescore(harness, mutate):
    harness.run_step()
    mutate(harness)
    out = harness.run_step()
    assert harness.calls["judge"] == 2
    assert out.data["computed"] == 1


def test_changed_metric_list_rescores(harness):
    harness.run_step()
    harness.run_step(metrics=["goal_success_rate", "task_completion"])
    assert harness.calls["judge"] == 2


def test_metric_order_does_not_matter(harness):
    harness.run_step(metrics=["a", "b"])
    harness.run_step(metrics=["b", "a"])
    assert harness.calls["judge"] == 1


def test_run_info_changes_do_not_invalidate(harness):
    harness.run_step()
    (harness.run / "run_info.json").write_text('{"timing": 123}', encoding="utf-8")
    harness.run_step()
    assert harness.calls["judge"] == 1


def test_legacy_file_is_adopted_not_rejudged(harness):
    harness.run_step()
    doc = _doc(harness)
    del doc["inputs_digest"]
    (harness.run / "metrics.json").write_text(json.dumps(doc), encoding="utf-8")
    out = harness.run_step()
    assert harness.calls["judge"] == 1
    assert out.metadata["reuse"] == "adopted legacy result"
    assert "inputs_digest" in _doc(harness)


def test_failed_scoring_is_not_reused(harness):
    harness.run_step()
    doc = _doc(harness)
    doc["run_quality"] = {"status": "error", "errors": ["boom"], "warnings": []}
    (harness.run / "metrics.json").write_text(json.dumps(doc), encoding="utf-8")
    harness.run_step()
    assert harness.calls["judge"] == 2


def test_overwrite_true_always_rescores(harness):
    harness.run_step()
    harness.run_step(overwrite=True)
    assert harness.calls["judge"] == 2


def test_offline_stub_results_are_not_reused_as_real(harness, monkeypatch):
    monkeypatch.setenv("MAS_MCE_OFFLINE", "1")
    harness.run_step()
    monkeypatch.delenv("MAS_MCE_OFFLINE")
    harness.run_step()
    assert harness.calls["judge"] == 2
