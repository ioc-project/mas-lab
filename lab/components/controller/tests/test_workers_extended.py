#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Worker and registry edge-case tests."""
from __future__ import annotations

import time

from mas.lab.controller.registry import WorkerRegistry
from mas.lab.controller.worker_model import WorkerKind, WorkerStatus
from mas.lab.controller.workers import WorkerRunner, run_pipeline_worker


def test_worker_model_helpers():
    from mas.lab.controller.worker_model import WorkerRecord, WorkerKind, WorkerStatus

    rec = WorkerRecord(id="w-1", kind=WorkerKind.APPLICATION)
    rec.append_stdout("line1")
    rec.append_stderr("err1")
    summary = rec.to_job_summary()
    assert summary["status"] == "pending"
    detail = rec.to_job_detail()
    assert "line1" in detail["stdout"]
    rec.status = WorkerStatus.FAILED
    rec.error = "llm_cache miss"
    assert rec._job_status() == "failed"
    assert rec.to_job_summary()["error"] == "llm_cache miss"
    reg = WorkerRegistry()
    rec = reg.create(WorkerKind.BENCHMARK, command="c")
    reg.update(rec.id, status=WorkerStatus.RUNNING)
    assert reg.get(rec.id).status == WorkerStatus.RUNNING
    assert reg.list_workers(kind=WorkerKind.BENCHMARK, status=WorkerStatus.RUNNING)


def test_pipeline_worker_submit(sample_lab, tmp_path):
    reg = WorkerRegistry()
    runner = WorkerRunner(reg)
    pipe = sample_lab / "pipelines" / "analysis.yaml"
    record = run_pipeline_worker(
        reg,
        runner,
        {"pipeline_yaml": str(pipe), "dry_run": True},
    )
    for _ in range(50):
        if record.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED):
            break
        time.sleep(0.1)
    assert record.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED)
