#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for job persistence, reconciliation, and ephemeral mode."""

from __future__ import annotations

from pathlib import Path

from mas.lab.controller.job_store import (
    EphemeralJobStore,
    SqliteJobStore,
)
from mas.lab.controller.jobs import (
    TERMINAL_STATUSES,
    Job,
    JobStatus,
    _jobs,
    load_jobs_from_store,
    now_iso,
    reconcile_jobs,
)

# ---------------------------------------------------------------------------
# SqliteJobStore unit tests
# ---------------------------------------------------------------------------


class TestSqliteJobStore:
    def test_save_and_load(self, tmp_path: Path):
        store = SqliteJobStore(tmp_path / "test.db")
        job_dict = {
            "id": "j1",
            "endpoint": "test/run",
            "command": "echo hello",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "request_body": {"app": "sre-triage", "workspace": "/tmp/w1"},
        }
        store.save(job_dict)

        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0]["id"] == "j1"
        assert loaded[0]["status"] == "pending"
        assert loaded[0]["request_body"]["app"] == "sre-triage"
        store.close()

    def test_upsert_updates_existing(self, tmp_path: Path):
        store = SqliteJobStore(tmp_path / "test.db")
        store.save({
            "id": "j1",
            "endpoint": "test/run",
            "command": "echo hello",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "request_body": {},
        })
        store.save({
            "id": "j1",
            "endpoint": "test/run",
            "command": "echo hello",
            "status": "running",
            "created_at": "2026-01-01T00:00:00Z",
            "started_at": "2026-01-01T00:00:01Z",
            "request_body": {},
        })

        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0]["status"] == "running"
        assert loaded[0]["started_at"] == "2026-01-01T00:00:01Z"
        store.close()

    def test_get_single_job(self, tmp_path: Path):
        store = SqliteJobStore(tmp_path / "test.db")
        store.save({
            "id": "j1",
            "endpoint": "test/run",
            "command": "cmd",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "request_body": {},
        })
        store.save({
            "id": "j2",
            "endpoint": "test/run",
            "command": "cmd2",
            "status": "failed",
            "created_at": "2026-01-01T00:00:01Z",
            "request_body": {},
        })

        result = store.get("j1")
        assert result is not None
        assert result["id"] == "j1"
        assert result["status"] == "completed"

        assert store.get("j999") is None
        store.close()

    def test_delete(self, tmp_path: Path):
        store = SqliteJobStore(tmp_path / "test.db")
        store.save({
            "id": "j1",
            "endpoint": "e",
            "command": "c",
            "status": "completed",
            "created_at": "t",
            "request_body": {},
        })
        store.delete("j1")
        assert store.get("j1") is None
        assert store.load_all() == []
        store.close()

    def test_creates_parent_dirs(self, tmp_path: Path):
        db_path = tmp_path / "deep" / "nested" / "dir" / "jobs.db"
        store = SqliteJobStore(db_path)
        assert db_path.parent.is_dir()
        store.close()

    def test_request_body_serialization(self, tmp_path: Path):
        store = SqliteJobStore(tmp_path / "test.db")
        body = {"app": "x", "overlays": ["a/b", "c/d"], "reps": 5}
        store.save({
            "id": "j1",
            "endpoint": "e",
            "command": "c",
            "status": "pending",
            "created_at": "t",
            "request_body": body,
        })
        loaded = store.get("j1")
        assert loaded is not None
        assert loaded["request_body"] == body
        store.close()

    def test_log_path_persisted(self, tmp_path: Path):
        store = SqliteJobStore(tmp_path / "test.db")
        store.save({
            "id": "j1",
            "endpoint": "e",
            "command": "c",
            "status": "completed",
            "created_at": "t",
            "request_body": {},
            "log_path": "/tmp/run.log",
        })
        loaded = store.get("j1")
        assert loaded is not None
        assert loaded["log_path"] == "/tmp/run.log"
        store.close()


# ---------------------------------------------------------------------------
# EphemeralJobStore
# ---------------------------------------------------------------------------


class TestEphemeralJobStore:
    def test_save_is_noop(self):
        store = EphemeralJobStore()
        store.save({"id": "j1", "status": "pending"})
        assert store.load_all() == []
        assert store.get("j1") is None

    def test_delete_is_noop(self):
        store = EphemeralJobStore()
        store.delete("j1")

    def test_close_is_noop(self):
        store = EphemeralJobStore()
        store.close()


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    def _make_job(self, **kwargs) -> Job:
        defaults = {
            "id": "j-test",
            "endpoint": "ioc/run",
            "command": "python3 run_ioc.py",
            "status": JobStatus.RUNNING,
            "created_at": now_iso(),
            "started_at": now_iso(),
            "request_body": {},
        }
        defaults.update(kwargs)
        return Job(**defaults)

    def test_running_job_becomes_interrupted_without_results(self, monkeypatch):
        """A running job with no results on disk → interrupted."""
        from mas.lab.controller import job_store as _store_mod

        store = EphemeralJobStore()
        monkeypatch.setattr(_store_mod, "_store", store)

        _jobs.clear()
        job = self._make_job(
            id="r1",
            status=JobStatus.RUNNING,
            request_body={"workspace": "/nonexistent/workspace"},
        )
        _jobs["r1"] = job

        reconcile_jobs()

        assert job.status == JobStatus.INTERRUPTED
        assert "server restarted" in (job.error or "").lower()
        assert job.finished_at is not None

    def test_running_job_becomes_completed_with_results(self, tmp_path: Path, monkeypatch):
        """A running job whose results exist on disk → completed."""
        from mas.lab.controller import job_store as _store_mod

        store = EphemeralJobStore()
        monkeypatch.setattr(_store_mod, "_store", store)

        workspace = tmp_path / "run-1"
        results_dir = workspace / "out" / "results"
        results_dir.mkdir(parents=True)
        (results_dir / "metrics_long.csv").write_text("metric,value\n", encoding="utf-8")

        _jobs.clear()
        job = self._make_job(
            id="r2",
            status=JobStatus.RUNNING,
            request_body={"workspace": str(workspace)},
        )
        _jobs["r2"] = job

        reconcile_jobs()

        assert job.status == JobStatus.COMPLETED
        assert job.error is None

    def test_pending_job_becomes_interrupted(self, monkeypatch):
        """A pending job with no process → interrupted."""
        from mas.lab.controller import job_store as _store_mod

        store = EphemeralJobStore()
        monkeypatch.setattr(_store_mod, "_store", store)

        _jobs.clear()
        job = self._make_job(id="p1", status=JobStatus.PENDING)
        _jobs["p1"] = job

        reconcile_jobs()
        assert job.status == JobStatus.INTERRUPTED

    def test_terminal_jobs_left_alone(self, monkeypatch):
        """Terminal jobs are not touched during reconciliation."""
        from mas.lab.controller import job_store as _store_mod

        store = EphemeralJobStore()
        monkeypatch.setattr(_store_mod, "_store", store)

        _jobs.clear()
        for status in TERMINAL_STATUSES:
            job = self._make_job(id=f"t-{status.value}", status=status)
            _jobs[job.id] = job

        reconcile_jobs()

        for status in TERMINAL_STATUSES:
            assert _jobs[f"t-{status.value}"].status == status


# ---------------------------------------------------------------------------
# load_jobs_from_store
# ---------------------------------------------------------------------------


class TestLoadJobsFromStore:
    def test_loads_all_jobs(self, tmp_path: Path, monkeypatch):
        from mas.lab.controller import job_store as _store_mod

        store = SqliteJobStore(tmp_path / "test.db")
        monkeypatch.setattr(_store_mod, "_store", store)

        store.save({
            "id": "j1",
            "endpoint": "test/run",
            "command": "echo hello",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "exit_code": 0,
            "request_body": {"app": "sre-triage"},
        })
        store.save({
            "id": "j2",
            "endpoint": "ioc/run",
            "command": "python3 run_ioc.py",
            "status": "failed",
            "created_at": "2026-01-02T00:00:00Z",
            "exit_code": 1,
            "error": "Something went wrong",
            "request_body": {},
        })

        _jobs.clear()
        load_jobs_from_store()

        assert len(_jobs) == 2
        assert _jobs["j1"].status == JobStatus.COMPLETED
        assert _jobs["j1"].request_body["app"] == "sre-triage"
        assert _jobs["j2"].status == JobStatus.FAILED
        assert _jobs["j2"].error == "Something went wrong"

        store.close()

    def test_unknown_status_defaults_to_failed(self, tmp_path: Path, monkeypatch):
        from mas.lab.controller import job_store as _store_mod

        store = SqliteJobStore(tmp_path / "test.db")
        monkeypatch.setattr(_store_mod, "_store", store)

        store.save({
            "id": "j-bad",
            "endpoint": "test",
            "command": "cmd",
            "status": "unknown_status",
            "created_at": "2026-01-01T00:00:00Z",
            "request_body": {},
        })

        _jobs.clear()
        load_jobs_from_store()

        assert _jobs["j-bad"].status == JobStatus.FAILED

        store.close()


# ---------------------------------------------------------------------------
# Job.to_dict log_path integration
# ---------------------------------------------------------------------------


class TestLogPathIntegration:
    def test_to_dict_reads_log_when_no_stdout(self, tmp_path: Path):
        log_file = tmp_path / "run.log"
        log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

        job = Job(
            id="log-test",
            endpoint="test",
            command="cmd",
            status=JobStatus.COMPLETED,
            created_at=now_iso(),
            log_path=str(log_file),
        )
        d = job.to_dict()
        assert "line1" in d["stdout"]
        assert "line3" in d["stdout"]

    def test_to_dict_prefers_in_memory_stdout(self, tmp_path: Path):
        log_file = tmp_path / "run.log"
        log_file.write_text("from-log", encoding="utf-8")

        job = Job(
            id="log-test",
            endpoint="test",
            command="cmd",
            status=JobStatus.COMPLETED,
            created_at=now_iso(),
            stdout="from-memory",
            log_path=str(log_file),
        )
        d = job.to_dict()
        assert d["stdout"] == "from-memory"

    def test_to_dict_handles_missing_log_file(self):
        job = Job(
            id="log-test",
            endpoint="test",
            command="cmd",
            status=JobStatus.COMPLETED,
            created_at=now_iso(),
            log_path="/nonexistent/path/run.log",
        )
        d = job.to_dict()
        assert d["stdout"] == ""


# ---------------------------------------------------------------------------
# End-to-end: persist across store instances (simulates restart)
# ---------------------------------------------------------------------------


class TestPersistenceAcrossRestart:
    def test_job_survives_store_reopen(self, tmp_path: Path, monkeypatch):
        """Save a job, close the store, reopen, and verify it's still there."""
        from mas.lab.controller import job_store as _store_mod

        db_path = tmp_path / "restart.db"

        store1 = SqliteJobStore(db_path)
        store1.save({
            "id": "survive-1",
            "endpoint": "ioc/run",
            "command": "python3 run_ioc.py --app sre-triage",
            "status": "completed",
            "created_at": "2026-08-19T10:00:00Z",
            "started_at": "2026-08-19T10:00:01Z",
            "finished_at": "2026-08-19T10:30:00Z",
            "exit_code": 0,
            "request_body": {
                "app": "sre-triage",
                "workspace": str(tmp_path / "ws"),
            },
            "log_path": str(tmp_path / "ws" / "out" / "run.log"),
        })
        store1.close()

        store2 = SqliteJobStore(db_path)
        monkeypatch.setattr(_store_mod, "_store", store2)

        _jobs.clear()
        load_jobs_from_store()

        assert "survive-1" in _jobs
        job = _jobs["survive-1"]
        assert job.status == JobStatus.COMPLETED
        assert job.request_body["app"] == "sre-triage"
        assert job.log_path == str(tmp_path / "ws" / "out" / "run.log")

        store2.close()

    def test_running_job_reconciled_after_reopen(self, tmp_path: Path, monkeypatch):
        """A running job is reconciled to interrupted after a store reopen."""
        from mas.lab.controller import job_store as _store_mod

        db_path = tmp_path / "restart.db"

        store1 = SqliteJobStore(db_path)
        store1.save({
            "id": "running-1",
            "endpoint": "ioc/run",
            "command": "python3 run_ioc.py",
            "status": "running",
            "created_at": "2026-08-19T10:00:00Z",
            "started_at": "2026-08-19T10:00:01Z",
            "request_body": {"workspace": str(tmp_path / "no-results")},
        })
        store1.close()

        store2 = SqliteJobStore(db_path)
        monkeypatch.setattr(_store_mod, "_store", store2)

        _jobs.clear()
        load_jobs_from_store()
        reconcile_jobs()

        job = _jobs["running-1"]
        assert job.status == JobStatus.INTERRUPTED

        reloaded = store2.get("running-1")
        assert reloaded is not None
        assert reloaded["status"] == "interrupted"

        store2.close()
