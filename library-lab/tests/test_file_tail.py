#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for the generic FileTailStep primitive, including the hardening
added during review: refusing to glob from filesystem root when a path
token resolves empty, and warning (once) when a configured pattern never
matches anything over a whole run."""

import json
import logging
from types import SimpleNamespace

import pytest

from mas.library.lab.steps.services.file_tail import (
    FileTailStep,
    append_custom_trace,
    resolve_path_pattern,
    tail_changed,
)


def test_tail_changed_detects_new_and_modified_files(tmp_path):
    f1 = tmp_path / "a.json"
    f1.write_text('{"v": 1}')
    seen: dict = {}
    changed = tail_changed(str(tmp_path / "*.json"), seen)
    assert [c[0] for c in changed] == [str(f1)]
    # unchanged on the next poll
    assert tail_changed(str(tmp_path / "*.json"), seen) == []
    # a real content/mtime change is picked up again
    f1.write_text('{"v": 2}')
    import os
    import time

    os.utime(f1, (time.time() + 1, time.time() + 1))
    changed2 = tail_changed(str(tmp_path / "*.json"), seen)
    assert len(changed2) == 1
    assert "2" in changed2[0][1]


def test_resolve_path_pattern_refuses_root_glob_on_unset_skill_state_dir(monkeypatch):
    monkeypatch.delenv("MAS_SKILL_STATE_DIR", raising=False)
    # Must NOT collapse to "/skill-a-state*.json" (an absolute-root glob) --
    # an empty substitution must produce no pattern at all.
    assert resolve_path_pattern("{skill_state_dir}/skill-a-state*.json") == ""


def test_resolve_path_pattern_substitutes_when_set(monkeypatch):
    monkeypatch.setenv("MAS_SKILL_STATE_DIR", "/tmp/some-session")
    assert resolve_path_pattern("{skill_state_dir}/skill-a-state*.json") == "/tmp/some-session/skill-a-state*.json"


def test_state_dir_token_refuses_without_any_state_dir_env(monkeypatch):
    monkeypatch.delenv("MAS_SKILL_STATE_DIR", raising=False)
    monkeypatch.delenv("MAS_ALPHA_STATE_DIR", raising=False)
    monkeypatch.delenv("MAS_BETA_STATE_DIR", raising=False)
    assert resolve_path_pattern("{state_dir}/state*.json", owner="alpha") == ""
    assert resolve_path_pattern("{state_dir}/.owner/state.json", owner="beta") == ""


def test_state_dir_token_falls_back_to_per_protocol_env_var(monkeypatch):
    """Even without MAS_SKILL_STATE_DIR, MAS_{OWNER}_STATE_DIR is enough."""
    monkeypatch.delenv("MAS_SKILL_STATE_DIR", raising=False)
    monkeypatch.setenv("MAS_ALPHA_STATE_DIR", "/tmp/alpha-session")
    assert (
        resolve_path_pattern("{state_dir}/alpha-state*.json", owner="alpha")
        == "/tmp/alpha-session/alpha-state*.json"
    )
    monkeypatch.delenv("MAS_ALPHA_STATE_DIR", raising=False)
    assert resolve_path_pattern("{state_dir}/alpha-state*.json", owner="alpha") == ""


def test_state_dir_token_prefers_skill_state_dir_over_per_protocol_override(monkeypatch):
    monkeypatch.setenv("MAS_SKILL_STATE_DIR", "/tmp/shared-session")
    monkeypatch.setenv("MAS_ALPHA_STATE_DIR", "/tmp/alpha-only-session")
    assert (
        resolve_path_pattern("{state_dir}/alpha-state*.json", owner="alpha")
        == "/tmp/shared-session/alpha-state*.json"
    )


def test_resolve_path_pattern_refuses_unset_run_dir():
    ctx = SimpleNamespace(output_dir=None)
    assert resolve_path_pattern("{run_dir}/trace.json", ctx) == ""


def test_file_tail_step_warns_once_on_missing_path_config(caplog):
    step = FileTailStep(name="tail", config={})
    ctx = SimpleNamespace(output_dir=None)
    with caplog.at_level(logging.WARNING):
        step.on_event({}, ctx)
        step.on_event({}, ctx)
    warnings = [r for r in caplog.records if "no 'path' configured" in r.message]
    assert len(warnings) == 1  # warned once, not spammed every event


def test_file_tail_step_warns_once_when_pattern_never_matches(tmp_path, caplog):
    step = FileTailStep(name="tail", config={"path": str(tmp_path / "nothing-*.json")})
    ctx = SimpleNamespace(output_dir=None)
    with caplog.at_level(logging.WARNING):
        for _ in range(25):
            step.on_event({}, ctx)
    warnings = [r for r in caplog.records if "has matched no files" in r.message]
    assert len(warnings) == 1


def test_file_tail_step_default_on_change_posts_raw_record(tmp_path, monkeypatch):
    posted = {}

    def fake_post(record, *, ref=None):
        posted["record"] = record

    monkeypatch.setattr("mas.library.lab.steps.services.file_tail.publish_custom_record", fake_post)
    step = FileTailStep(name="tail", config={"path": str(tmp_path / "*.txt"), "owner": "myscript"})
    (tmp_path / "out.txt").write_text("hello")
    ctx = SimpleNamespace(output_dir=None)
    step.on_event({}, ctx)
    assert posted["record"]["kind"] == "custom.myscript.raw"
    assert posted["record"]["content"] == "hello"


def test_file_tail_step_on_change_exception_does_not_propagate(tmp_path, caplog):
    class BoomStep(FileTailStep):
        def on_change(self, path, content, ctx):
            raise RuntimeError("boom")

    step = BoomStep(name="tail", config={"path": str(tmp_path / "*.txt")})
    (tmp_path / "out.txt").write_text("x")
    ctx = SimpleNamespace(output_dir=None)
    with caplog.at_level(logging.WARNING):
        step.on_event({}, ctx)  # must not raise
    assert any("on_change failed" in r.message for r in caplog.records)


def test_append_custom_trace_writes_jsonl_with_timestamp(tmp_path):
    ctx = SimpleNamespace(output_dir=tmp_path)
    append_custom_trace(ctx, "skill-a", {"kind": "custom.skill-a.panel", "x": 1})
    path = tmp_path / "custom.skill-a.jsonl"
    line = json.loads(path.read_text().splitlines()[0])
    assert line["x"] == 1
    assert "ts" in line


def test_append_custom_trace_noop_without_output_dir():
    ctx = SimpleNamespace(output_dir=None)
    # Must not raise even though there is nowhere to write.
    append_custom_trace(ctx, "skill-a", {"kind": "custom.skill-a.panel"})


@pytest.mark.asyncio
async def test_execute_reports_when_nothing_was_ever_tailed(tmp_path, caplog):
    step = FileTailStep(name="tail", config={"path": str(tmp_path / "nothing-*.json")})
    ctx = SimpleNamespace(output_dir=None)
    with caplog.at_level(logging.INFO):
        out = await step.execute(ctx)
    assert out.data["tailed"] == []
    assert any("never matched a file" in r.message for r in caplog.records)
