#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Live regression for mas-lab#65 — unbounded stacked ``get_metrics`` retries.

Requires the Concord lab checkout (``ioc-core-l9-main/mas-lab/labs/concord``) and
``OUTSHIFT_GLS_API_KEY``. Runs:

  mas-lab benchmark run experiments-cc3-gls-regression.yaml --infra gls-vllm

and asserts ``context_assembled`` events never carry more than
``working_memory_slice_limit`` copies of the same failed ``get_metrics`` tool
result in one assembly. Before the fix, in-turn working memory was pinned in
full with no cap, so a stuck retry loop could stack every single repeat.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAS_LAB = Path(sys.executable).parent / "mas-lab"

_SERVICE_MISSING = "required parameter 'service' is missing"


def _concord_lab_root() -> Path | None:
    candidate = REPO_ROOT.parent / "ioc-core-l9-main/mas-lab/labs/concord"
    exp = candidate / "experiments-cc3-gls-regression.yaml"
    infra = candidate / "infra/gls-vllm.yaml"
    if exp.is_file() and infra.is_file():
        return candidate
    return None


def _max_stacked_service_missing_tool_messages(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "tool" and _SERVICE_MISSING in str(m.get("content") or ""))


def _scan_events_for_stacked_service_errors(events_paths: list[Path]) -> tuple[int, int]:
    """Return (max stacked service-missing tool msgs in one assembly, assembly count)."""
    max_stacked = 0
    assemblies = 0
    for path in events_paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("kind") != "context_assembled":
                continue
            assemblies += 1
            msgs = evt.get("messages") or []
            max_stacked = max(max_stacked, _max_stacked_service_missing_tool_messages(msgs))
    return max_stacked, assemblies


@pytest.fixture
def concord_cc3_experiment() -> Path:
    root = _concord_lab_root()
    if root is None:
        pytest.skip(
            "Concord lab not found at ../ioc-core-l9-main/mas-lab/labs/concord "
            "(need experiments-cc3-gls-regression.yaml and infra/gls-vllm.yaml)"
        )
    return root / "experiments-cc3-gls-regression.yaml"


def test_cc3_gls_regression_experiment_dry_run(concord_cc3_experiment: Path) -> None:
    if not MAS_LAB.is_file():
        pytest.skip("mas-lab CLI not in venv")
    proc = subprocess.run(
        [
            str(MAS_LAB),
            "benchmark",
            "run",
            str(concord_cc3_experiment),
            "--dry-run",
            "--infra",
            "gls-vllm",
        ],
        cwd=str(concord_cc3_experiment.parent),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Configuration valid" in proc.stdout + proc.stderr


@pytest.mark.timeout(900)
def test_cc3_gls_live_run_does_not_stack_unbounded_service_missing_errors(
    concord_cc3_experiment: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce CC-3 on Gemma; prove assembly caps stacked tool errors at the
    configured working-memory limit instead of growing without bound.
    """
    if os.environ.get("RUN_ISSUE_65_LIVE", "").strip() not in ("1", "true", "yes"):
        pytest.skip("set RUN_ISSUE_65_LIVE=1 to run live CC-3 Gemma benchmark")
    gls_key = os.environ.get("OUTSHIFT_GLS_API_KEY", "").strip()
    if not gls_key:
        pytest.skip("OUTSHIFT_GLS_API_KEY not set — live Gemma regression skipped")

    pytest.importorskip("mas.lab.benchmark.worker")
    from mas.lab.benchmark.golden.cache_backup import find_events_in_tree
    from mas.lab.benchmark.worker import run_benchmark_sync
    from mas.library.standard.lib.context.working_memory import working_memory_slice_limit

    out = tmp_path / "benchmark-out"
    trace_cache = tmp_path / "trace-cache"
    trace_cache.mkdir()
    mas_home = tmp_path / "mas-home"
    mas_home.mkdir()
    monkeypatch.setenv("MAS_HOME", str(mas_home))
    monkeypatch.setenv("MAS_TRACE_CACHE", str(trace_cache))

    ok = run_benchmark_sync(
        concord_cc3_experiment,
        force=True,
        max_runs=1,
        output_dir=out,
        trace_cache_dir=trace_cache,
        infra_name="gls-vllm",
        flavour_name="local",
    )
    assert ok, "CC-3 Gemma benchmark run failed — see logs above"

    events = find_events_in_tree(out) or find_events_in_tree(trace_cache)
    assert events, "expected events.jsonl from benchmark run"

    max_stacked, assemblies = _scan_events_for_stacked_service_errors(events)
    assert assemblies > 0, "expected context_assembled events in trace"
    limit_group_count = max(1, working_memory_slice_limit(None) // 2)
    assert max_stacked <= limit_group_count, (
        f"issue #65 regression: saw {max_stacked} stacked "
        f"'{_SERVICE_MISSING}' tool messages in one context_assembled "
        f"(working memory should cap retries instead of growing unbounded)"
    )
