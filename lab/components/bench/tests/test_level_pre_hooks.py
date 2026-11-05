#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Level ``pre`` semantics, end to end on the lab-smoke experiment.

``pre`` of a level runs before any run under it starts and only when a run
beneath it will execute:

* ``experiment.pre`` once, ``scenario.pre`` once per scenario, ``item.pre`` once
  per item, ``run.pre`` before every run.
* Never by scanning run folders on disk: a clean directory used to expand
  ``run.pre`` to nothing, a rerun expanded it over stale folders.
* A run served from the trace cache executes nothing, so nothing under it is
  prepared.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from mas.lab.benchmark.pipeline import PipelineStep, StepOutput, register_step_type

from . import test_golden_pipeline_path as _golden

REPO_ROOT = _golden.REPO_ROOT
smoke_env = _golden.smoke_env  # re-exported pytest fixture

FIXTURES = REPO_ROOT / "tests/fixtures/lab-smoke"


class _RecordPre(PipelineStep):
    type = "record_pre"

    async def execute(self, ctx) -> StepOutput:
        cfg = self.config
        if cfg.get("fail"):
            raise RuntimeError(f"{self.name} refuses")
        with Path(cfg["log"]).open("a", encoding="utf-8") as fh:
            fh.write(f"{self.name}|{cfg.get('scenario', '')}|{cfg.get('test', '')}|{cfg.get('run', '')}\n")
        return StepOutput(data={})


register_step_type("record_pre", _RecordPre)


def _experiment(
    tmp_path: Path, *, log: Path, n_runs: int = 2, cache: str = "disabled", fail_run_pre: bool = False
) -> Path:
    exp = tmp_path / "experiment.yaml"
    run_fail = "          fail: true\n" if fail_run_pre else ""
    exp.write_text(
        f"""experiment:
  name: pre-hooks
  default_flavour: local
  application:
    manifest: {REPO_ROOT / "docs/tutorials/01-building-an-agent/agent.yaml"}
    configs_dir: {REPO_ROOT / "docs/tutorials/01-building-an-agent/overlays"}
  scenarios:
    - id: tools
      overlays:
        logic:
          - tools
          - ref: {REPO_ROOT / "tests/fixtures/golden-runs/hitl-system-tools.yaml"}
        control: []
        infra: []
  dataset:
    path: {FIXTURES / "dataset.yaml"}
    limit: 1
  bench_emulation:
    runtime:
      cache: {cache}
  pre:
    - {{name: exp-pre, type: record_pre, config: {{log: '{log}'}}}}
  scenario:
    pre:
      - {{name: scenario-pre, type: record_pre, config: {{log: '{log}'}}}}
  item:
    pre:
      - {{name: item-pre, type: record_pre, config: {{log: '{log}'}}}}
  run:
    n_runs: {n_runs}
    pre:
      - name: run-pre
        type: record_pre
        config:
          log: '{log}'
{run_fail}""",
        encoding="utf-8",
    )
    return exp


def _run(exp: Path, smoke_env, **kw) -> bool:
    from mas.lab.benchmark.worker import run_benchmark_sync

    output_dir, trace_cache = smoke_env
    return run_benchmark_sync(exp, output_dir=output_dir, trace_cache_dir=trace_cache, **kw)


def _lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _base(line: str) -> str:
    """Instance name without its ``-{scenario}-{item}-{run}`` suffix."""
    name = line.split("|")[0]
    for base in ("exp-pre", "scenario-pre", "item-pre", "run-pre"):
        if name == base or name.startswith(base + "-"):
            return base
    return name


@pytest.mark.timeout(240)
def test_each_level_pre_runs_at_its_own_granularity(smoke_env, tmp_path) -> None:
    log = tmp_path / "pre.log"
    assert _run(_experiment(tmp_path, log=log), smoke_env, force=True)

    lines = _lines(log)
    names = [_base(ln) for ln in lines]
    assert names.count("exp-pre") == 1
    assert names.count("scenario-pre") == 1
    assert names.count("item-pre") == 1
    assert names.count("run-pre") == 2  # clean dir: used to expand to zero
    assert {ln.split("|")[3] for ln in lines if _base(ln) == "run-pre"} == {"r1", "r2"}
    # experiment, then scenario, then item, then the runs
    assert names.index("exp-pre") < names.index("scenario-pre") < names.index("item-pre") < names.index("run-pre")
    assert lines[names.index("run-pre")].split("|")[1:3] == ["tools", "item1"]
    assert lines[names.index("scenario-pre")].split("|")[1:] == ["tools", "", ""]


@pytest.mark.timeout(300)
def test_cached_runs_prepare_nothing_but_the_experiment(smoke_env, tmp_path) -> None:
    log = tmp_path / "pre.log"
    exp = _experiment(tmp_path, log=log, cache="content-addressed")
    assert _run(exp, smoke_env)  # live: everything prepared
    first = [_base(ln) for ln in _lines(log)]
    assert first.count("run-pre") == 2

    log.unlink()
    assert _run(exp, smoke_env)  # no --force: both runs are trace-cache hits
    second = [_base(ln) for ln in _lines(log)]
    assert second == ["exp-pre"]


@pytest.mark.timeout(240)
def test_failing_run_pre_fails_the_run_and_never_starts_the_mas(smoke_env, tmp_path) -> None:
    log = tmp_path / "pre.log"
    exp = _experiment(tmp_path, log=log, n_runs=1, fail_run_pre=True)
    assert _run(exp, smoke_env, force=True) is False

    output_dir, trace_cache = smoke_env
    rows = list(csv.DictReader((output_dir / "results.csv").open(encoding="utf-8")))
    assert [r["status"] for r in rows] == ["error"]
    assert "run.pre failed" in rows[0]["error"] and "refuses" in rows[0]["error"]
    assert not list(trace_cache.rglob("events.jsonl")), "the MAS must not start on unprepared state"
