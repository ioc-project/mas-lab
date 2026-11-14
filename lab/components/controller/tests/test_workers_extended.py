#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Worker and registry edge-case tests."""

from __future__ import annotations

import time

from mas.lab.controller.registry import WorkerRegistry
from mas.lab.controller.worker_model import WorkerStatus
from mas.lab.controller.workers import WorkerRunner, run_pipeline_worker


def test_worker_model_helpers():
    from mas.lab.controller.worker_model import WorkerKind, WorkerRecord, WorkerStatus

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


def test_pipeline_worker_resolves_a_lab_library_step_from_another_cwd(tmp_path, monkeypatch) -> None:
    """The daemon ``/api/pipeline/run`` seam: cwd is not the lab."""
    lab = tmp_path / "worker.lab"
    pkg = lab / "worker_lab"
    pkg.mkdir(parents=True)
    (lab / "lab-config.yaml").write_text(
        "lab:\n  name: worker\n  libraries:\n    - worker_lab/\n",
        encoding="utf-8",
    )
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "steps.py").write_text(
        "from mas.lab.benchmark.pipeline.core import PipelineStep\n"
        "from mas.lab.benchmark.pipeline.models import StepOutput\n\n"
        "class WorkerLabStep(PipelineStep):\n"
        "    type = 'worker_lab_step'\n\n"
        "    async def execute(self, ctx):\n"
        "        return StepOutput()\n",
        encoding="utf-8",
    )
    (pkg / "library.yaml").write_text(
        "apiVersion: mas/v1\nkind: Library\nname: worker-lab\ntypes:\n  - step\nplugins:\n"
        "  - type: step\n    name: worker_lab_step\n    module: worker_lab.steps\n"
        "    class: WorkerLabStep\n",
        encoding="utf-8",
    )
    pipelines = lab / "pipelines"
    pipelines.mkdir()
    pipe = pipelines / "post.yaml"
    pipe.write_text(
        "pipeline:\n  name: post\n  steps:\n  - name: probe\n    type: worker_lab_step\n    depends_on: []\n",
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    from mas.lab.benchmark.pipeline.core import get_step

    assert get_step("worker_lab_step") is None

    reg = WorkerRegistry()
    runner = WorkerRunner(reg)
    record = run_pipeline_worker(
        reg,
        runner,
        {"pipeline_yaml": str(pipe), "dry_run": True},
    )
    for _ in range(50):
        if record.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED):
            break
        time.sleep(0.1)
    assert record.status == WorkerStatus.COMPLETED, record.error
