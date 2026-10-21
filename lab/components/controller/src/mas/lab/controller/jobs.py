#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Background job tracking and execution."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.TIMEOUT,
    JobStatus.INTERRUPTED,
}


def _job_origin(request_body: dict | None) -> str | None:
    body = request_body or {}
    ref = body.get("demo_ui_ref")
    if isinstance(ref, dict):
        origin = ref.get("origin")
        return str(origin) if origin else None
    return None


@dataclass
class Job:
    id: str
    endpoint: str
    command: str
    status: JobStatus
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    response: str = ""
    error_message: str = ""
    error_detail: str = ""
    session_id: str = ""
    request_body: dict = field(default_factory=dict)
    log_path: Optional[str] = None
    _proc: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        stdout = self.stdout
        if not stdout and self.log_path:
            try:
                stdout = Path(self.log_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                stdout = ""
        return {
            "id": self.id,
            "endpoint": self.endpoint,
            "command": self.command,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "stdout": stdout,
            "stderr": self.stderr,
            "error": self.error,
            "response": self.response,
            "error_message": self.error_message,
            "error_detail": self.error_detail,
            "session_id": self.session_id,
            "request_body": self.request_body,
            "log_path": self.log_path,
        }

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "endpoint": self.endpoint,
            "command": self.command,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "origin": _job_origin(self.request_body),
        }


_jobs: dict[str, Job] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_job(
    job: Job,
    cmd: list[str],
    cwd: Path,
    timeout: int,
    env: dict,
    cleanup_paths: list[Path] | None = None,
) -> None:
    """Background coroutine that executes the command and updates the job."""
    job.status = JobStatus.RUNNING
    job.started_at = now_iso()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        job.pid = proc.pid
        job._proc = proc

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        job.exit_code = proc.returncode
        job.stdout = stdout.decode(errors="replace")
        job.stderr = stderr.decode(errors="replace")
        job.status = JobStatus.COMPLETED if proc.returncode == 0 else JobStatus.FAILED
        if proc.returncode != 0:
            job.error = f"Command failed with exit code {proc.returncode}"

    except asyncio.TimeoutError:
        if job._proc:
            job._proc.kill()
            await job._proc.communicate()
        job.status = JobStatus.TIMEOUT
        job.error = f"Command timed out after {timeout}s"

    except asyncio.CancelledError:
        if job._proc and job._proc.returncode is None:
            job._proc.kill()
            await job._proc.communicate()
        job.status = JobStatus.CANCELLED
        job.error = "Job was cancelled"

    except FileNotFoundError:
        job.status = JobStatus.FAILED
        job.error = f"Command not found: {cmd[0]}. Ensure mas-runtime is on PATH."

    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)

    finally:
        job.finished_at = now_iso()
        job._proc = None
        _persist(job)
        if cleanup_paths:
            for p in cleanup_paths:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)


async def run_agent_chat_job(
    job: Job,
    manifest_yaml: str,
    query: str,
    lib_dir: Path,
    flavour: Optional[str],
    session_id: str,
    timeout: int,
) -> None:
    """Background coroutine: run one agent turn in-process (no subprocess)."""
    from mas.lab.controller.agent_chat import run_agent_turn

    job.status = JobStatus.RUNNING
    job.started_at = now_iso()

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                run_agent_turn,
                manifest_yaml,
                query,
                base_dir=lib_dir,
                flavour=flavour,
                session_id=session_id,
            ),
            timeout=timeout,
        )
        job.response = result.response
        job.error_message = result.error_message
        job.error_detail = result.error_detail
        job.session_id = result.session_id

        if result.status == "ok":
            job.status = JobStatus.COMPLETED
            job.exit_code = 0
        else:
            job.status = JobStatus.FAILED
            job.exit_code = 1
            job.error = result.error_message

    except asyncio.TimeoutError:
        job.status = JobStatus.TIMEOUT
        job.error = f"Agent timed out after {timeout}s"
        job.error_message = job.error

    except asyncio.CancelledError:
        job.status = JobStatus.CANCELLED
        job.error = "Job was cancelled"
        job.error_message = job.error

    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)
        job.error_message = str(exc)

    finally:
        job.finished_at = now_iso()
        _persist(job)


def submit_agent_chat_job(
    endpoint: str,
    manifest_yaml: str,
    query: str,
    lib_dir: Path,
    flavour: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout: int = 60,
    request_body: dict | None = None,
) -> Job:
    """Create a chat job, launch in-process agent turn, return immediately."""
    sid = session_id or str(uuid.uuid4())

    job = Job(
        id=str(uuid.uuid4()),
        endpoint=endpoint,
        command=f"agent-chat({lib_dir.name})",
        status=JobStatus.PENDING,
        created_at=now_iso(),
        session_id=sid,
        request_body=request_body or {},
    )
    _jobs[job.id] = job

    task = asyncio.create_task(
        run_agent_chat_job(
            job, manifest_yaml, query, lib_dir, flavour, sid, timeout,
        )
    )
    job._task = task

    logger.info("Agent chat job %s submitted (session=%s)", job.id, sid)
    _persist(job)
    return job


def submit_job(
    endpoint: str,
    cmd: list[str],
    cwd: Path,
    timeout: int = 60,
    env_override: dict[str, str] | None = None,
    request_body: dict | None = None,
    cleanup_paths: list[Path] | None = None,
) -> Job:
    """Create a job, launch it in the background, return immediately."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    job = Job(
        id=str(uuid.uuid4()),
        endpoint=endpoint,
        command=" ".join(cmd),
        status=JobStatus.PENDING,
        created_at=now_iso(),
        request_body=request_body or {},
    )
    _jobs[job.id] = job

    task = asyncio.create_task(run_job(job, cmd, cwd, timeout, env, cleanup_paths))
    job._task = task

    logger.info("Job %s submitted: %s", job.id, job.command)
    _persist(job)
    return job


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def _persist(job: Job) -> None:
    try:
        from mas.lab.controller.job_store import get_job_store

        get_job_store().save(job.to_dict())
    except Exception as exc:
        logger.debug("job persist skipped: %s", exc)


def load_jobs_from_store() -> None:
    """Hydrate the in-memory map from the durable store (controller restart)."""
    from mas.lab.controller.job_store import get_job_store

    for record in get_job_store().load_all():
        status_raw = str(record.get("status") or "failed")
        try:
            status = JobStatus(status_raw)
        except ValueError:
            status = JobStatus.FAILED
        job = Job(
            id=str(record.get("id") or ""),
            endpoint=str(record.get("endpoint") or ""),
            command=str(record.get("command") or ""),
            status=status,
            created_at=str(record.get("created_at") or now_iso()),
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
            pid=record.get("pid"),
            exit_code=record.get("exit_code"),
            error=record.get("error"),
            request_body=record.get("request_body") or {},
            log_path=record.get("log_path"),
        )
        if job.id:
            _jobs[job.id] = job


def reconcile_jobs() -> None:
    """After a restart, mark in-flight jobs interrupted unless results exist."""
    for job in list(_jobs.values()):
        if job.status in TERMINAL_STATUSES:
            continue
        workspace = (job.request_body or {}).get("workspace")
        completed = False
        if workspace:
            results = Path(workspace) / "out" / "results"
            if results.is_dir() and any(results.iterdir()):
                completed = True
        if completed:
            job.status = JobStatus.COMPLETED
            job.finished_at = now_iso()
        else:
            job.status = JobStatus.INTERRUPTED
            job.error = "Job interrupted because the server restarted"
            job.finished_at = now_iso()
        _persist(job)
