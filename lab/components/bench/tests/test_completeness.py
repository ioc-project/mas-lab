#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Completeness: incomplete runs are never complete and never reach a batch stage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mas.lab.benchmark.execution.completeness import (
    STAGE_BATCH,
    STAGE_LIVE,
    STAGE_MIXED,
    edge_rule,
    invoke_is_complete,
    normalize_stage_mode,
    result_file_is_complete,
    run_dir_is_complete,
)
from mas.lab.benchmark.execution.retry import lab_run_error_is_retryable, retry_after_seconds
from mas.lab.benchmark.execution.roles import resolve_executor
from mas.library.lab.plugins.executors.native import NativeExecutor


def test_result_json_error_is_not_complete(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "error", "error": "timeout"}), encoding="utf-8")
    assert not result_file_is_complete(path)
    assert not run_dir_is_complete(tmp_path)


def test_result_json_ok_is_complete(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    assert result_file_is_complete(path)
    assert run_dir_is_complete(tmp_path)


def test_local_events_without_ok_result_are_not_complete(tmp_path: Path) -> None:
    events = tmp_path / "traces"
    events.mkdir()
    (events / "events.jsonl").write_text('{"kind": "llm_call_end"}\n', encoding="utf-8")
    (tmp_path / "result.json").write_text(json.dumps({"status": "error", "error": "boom"}), encoding="utf-8")
    assert not run_dir_is_complete(tmp_path)


def test_invoke_is_complete_never_lies() -> None:
    assert invoke_is_complete(status="ok")
    assert not invoke_is_complete(status="error", error="timeout")
    assert not invoke_is_complete(status="error", error="worker lost")
    plugin = NativeExecutor()
    assert plugin.is_complete({"status": "ok"}, status="ok")
    assert not plugin.is_complete({"status": "error"}, status="error", error="timeout")
    resolved = resolve_executor("native")
    assert not resolved.is_complete({}, status="error", error="LLM request failed:")


def test_auth_and_tls_are_not_retried() -> None:
    assert not lab_run_error_is_retryable("TLS certificate verification failed", status="error")
    assert not lab_run_error_is_retryable("HTTP 403 authentication/authorization", status="error")
    assert not lab_run_error_is_retryable("malformed spec: missing scenarios", status="error")
    assert lab_run_error_is_retryable("LLM request failed: HTTP 503", status="error")


def test_retry_after_lengthens_backoff() -> None:
    assert retry_after_seconds("rate limited retry-after: 7") == 7.0
    assert retry_after_seconds("retry-after-ms: 1500") == 1.5
    assert retry_after_seconds("no hint") is None


def test_stage_mode_vocabulary() -> None:
    assert normalize_stage_mode(None, realtime=None, folds=False) == STAGE_BATCH
    assert normalize_stage_mode(None, realtime=True, folds=False) == STAGE_LIVE
    assert normalize_stage_mode(None, realtime=None, folds=True) == STAGE_LIVE
    assert normalize_stage_mode("mixed", realtime=None, folds=True) == STAGE_MIXED
    with pytest.raises(ValueError, match="batch"):
        normalize_stage_mode("batch", realtime=True, folds=False)


def test_only_a_batch_consumer_waits_for_a_complete_run() -> None:
    assert edge_rule(STAGE_LIVE, STAGE_BATCH) == "completeness_gate"
    assert edge_rule(STAGE_BATCH, STAGE_BATCH) == "complete"
    assert edge_rule(STAGE_MIXED, STAGE_BATCH) == "complete"
    for producer in (STAGE_BATCH, STAGE_LIVE, STAGE_MIXED):
        assert edge_rule(producer, STAGE_LIVE) == edge_rule(producer, STAGE_MIXED) == "any"


def test_a_local_error_wins_over_a_cached_ok(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    (cache / "abcdef12").mkdir(parents=True)
    (cache / "abcdef12" / "result.json").write_text('{"status": "ok"}', encoding="utf-8")
    monkeypatch.setattr("mas.lab.paths.trace_cache", lambda: cache)
    run = tmp_path / "run"
    run.mkdir()
    (run / ".run_ref").write_text("abcdef12", encoding="utf-8")
    assert run_dir_is_complete(run)  # no local result: the cache twin answers
    (run / "result.json").write_text('{"status": "error"}', encoding="utf-8")
    assert not run_dir_is_complete(run)


@pytest.mark.asyncio
async def test_executor_incomplete_result_is_retried_then_stops() -> None:
    from mas.lab.benchmark.execution.retry import run_with_retrier
    from mas.lab.benchmark.execution.roles import AttemptResult, resolve_role_plugin

    calls = {"n": 0}

    class Picky:
        plugin_id = "picky"

        def validate_params(self, params) -> None: ...

        def is_complete(self, result_dict, *, status: str, error: str = "") -> bool:
            return calls["n"] >= 2

        async def attempt(self, do_run, *, before_attempt=None):
            calls["n"] += 1
            return AttemptResult({}, "ok", "", "done")

    retrier = resolve_role_plugin("retrier", "native")
    retrier.validate_params({"max_attempts": 3, "backoff_s": 0})
    out = await run_with_retrier(Picky(), retrier, lambda: {})
    assert (out.status, out.attempts, calls["n"]) == ("ok", 2, 2)
