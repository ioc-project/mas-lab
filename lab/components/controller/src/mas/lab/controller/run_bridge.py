#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Mirror controller workers into the HTTP run store so CLI and UI share one catalog."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

from mas.lab.controller.api import peek_controller_api
from mas.lab.controller.run_store import _now, get_run_store, new_run

_TERMINAL = {"completed", "failed", "cancelled"}
_dropped: set[str] = set()
_cleanup_by_run: dict[str, list[Path]] = {}
_lock = threading.RLock()

_WORKER_KIND_TO_ITEM = {
    "benchmark": "experiment",
    "experiment": "experiment",
    "pipeline": "pipeline",
    "application": "application",
}


def submit_run(api, req, path: Path, cwd: Path) -> dict[str, Any]:
    """Submit the same worker the CLI uses for this kind."""
    ref = {
        "origin": "ui",
        "scope": req.scope,
        "kind": req.kind,
        "item": req.item,
    }
    common = {
        "cwd": str(cwd),
        "scope": req.scope,
        "item": req.item,
        "demo_ui_ref": ref,
    }
    if req.kind == "experiment":
        spec = {
            **common,
            "experiment_yaml": str(path),
            "progress": True,
        }
        return api.submit_benchmark(spec)
    if req.kind == "pipeline":
        spec = {**common, "pipeline_yaml": str(path)}
        return api.submit_pipeline(spec)
    spec = {
        **common,
        "spec_path": str(path),
        "prompt": req.query or "",
        "flavour": req.bind,
        "config": {"overlays": list(req.overlays or [])},
        "command": f"mas-ctl run-mas {path}",
    }
    return api.submit_application(spec)


def apply_worker_to_run(run, detail: dict[str, Any], *, cwd: Path | None = None) -> None:  # noqa: ARG001
    status = str(detail.get("status") or "running")
    run.status = {
        "pending": "pending",
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "failed",
    }.get(status, status)
    run.exit_code = detail.get("exit_code")
    if detail.get("started_at"):
        run.started_at = detail["started_at"]
    elif run.status == "running" and not run.started_at:
        run.started_at = _now()
    run.finished_at = detail.get("finished_at")
    lines: list[str] = []
    query = (run.request_summary or {}).get("query")
    if query:
        lines.append(str(query))
    stdout = (detail.get("stdout") or "").rstrip()
    stderr = (detail.get("stderr") or "").rstrip()
    error = (detail.get("error") or "").rstrip()
    if stdout:
        lines.extend(stdout.splitlines())
    if stderr:
        lines.extend(stderr.splitlines())
    if error:
        lines.append(error)
    progress = progress_line(detail.get("request_body") or {})
    if progress:
        lines = [line for line in lines if not line.startswith("ok=")]
        lines.append(progress)
    run.log_lines = lines
    spec = detail.get("request_body") or {}
    output_dir = spec.get("output_dir")
    if output_dir:
        run.result_dir = str(output_dir)


def progress_line(spec: dict[str, Any]) -> str | None:
    """Same tally ``mas-lab benchmark follow`` prints from ``run_info.json``."""
    out = spec.get("output_dir")
    if not out:
        yaml_path = spec.get("experiment_yaml")
        if yaml_path:
            out = str(Path(yaml_path).parent / "output")
    if not out:
        return None
    root = Path(out)
    if not root.is_dir():
        return None
    counts: dict[str, int] = {}
    for path in root.rglob("run_info.json"):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            status = str(info.get("status", "unknown"))
        except Exception:
            status = "unknown"
        counts[status] = counts.get(status, 0) + 1
    if not counts:
        return None
    done = counts.get("ok", 0)
    err = counts.get("error", 0)
    return f"ok={done}  err={err}  done={done + err}"


def locate_scope_item(path: Path, store, kind: str) -> tuple[str, str]:
    from mas.lab.controller.manifest_layout import layout_for
    from mas.lab.controller.scope_catalog import get_scope_catalog

    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        catalog = get_scope_catalog(store)
        layout = layout_for(kind)
    except Exception:
        return "", path.stem
    for scope in catalog.all():
        if kind not in (scope.kinds or []):
            continue
        try:
            located = layout.list_located(scope)
        except Exception:
            continue
        for item in located:
            try:
                if item.manifest_path.resolve() == resolved:
                    return scope.id, item.item_id
            except OSError:
                continue
        try:
            resolved.relative_to(scope.root.resolve())
            return scope.id, path.stem
        except ValueError:
            continue
    return "", path.stem


def run_from_worker(detail: dict[str, Any], store) -> Any:
    spec = detail.get("request_body") or {}
    worker_kind = str(detail.get("kind") or spec.get("kind") or "")
    kind = _WORKER_KIND_TO_ITEM.get(worker_kind, worker_kind or "experiment")
    scope = str(spec.get("scope") or "")
    item = str(spec.get("item") or "")
    ref = spec.get("demo_ui_ref") if isinstance(spec.get("demo_ui_ref"), dict) else {}
    if not scope:
        scope = str(ref.get("scope") or "")
    if not item:
        item = str(ref.get("item") or "")
    if ref.get("kind"):
        kind = str(ref["kind"])
    path_key = {
        "experiment": "experiment_yaml",
        "pipeline": "pipeline_yaml",
        "application": "spec_path",
    }.get(kind)
    path = Path(spec[path_key]) if path_key and spec.get(path_key) else None
    if (not scope or not item) and path is not None:
        found_scope, found_item = locate_scope_item(path, store, kind)
        scope = scope or found_scope
        item = item or found_item
    if not item:
        item = path.stem if path is not None else (detail.get("id") or "run")
    if not scope:
        scope = "library:unknown"
    source = "ui" if ref.get("origin") == "ui" else "cli"
    run = new_run(
        scope=scope,
        kind=kind,
        item=item,
        request_summary={
            "source": source,
            "command": detail.get("command") or "",
            "action": "run",
        },
    )
    worker_id = detail.get("id")
    if worker_id:
        run.id = str(worker_id)
        run.job_id = str(worker_id)
    apply_worker_to_run(run, detail, cwd=Path(spec["cwd"]) if spec.get("cwd") else None)
    return run


def refresh_run(run) -> None:
    api = peek_controller_api()
    if api is None or not run.job_id:
        return
    detail = api.get_worker(run.job_id)
    if detail is None:
        return
    cwd = None
    spec = detail.get("request_body") or {}
    if spec.get("cwd"):
        cwd = Path(spec["cwd"])
    apply_worker_to_run(run, detail, cwd=cwd)
    if run.status not in {"pending", "running"}:
        from mas.lab.controller.routes.runs import _attach_artifacts

        _attach_artifacts(run, cwd)
        _finish_cleanup(run.id)


def sync_workers(store) -> None:
    api = peek_controller_api()
    if api is None:
        return
    runs = get_run_store()
    with _lock:
        dropped = set(_dropped)
    by_job = {row.job_id: row for row in runs.list() if row.job_id}
    for summary in api.list_workers():
        worker_id = summary.get("id")
        if not worker_id or worker_id in dropped:
            continue
        detail = api.get_worker(worker_id) or summary
        # ``list_workers`` summaries omit kind/spec; prefer the detail record.
        if "kind" not in detail and "kind" in summary:
            detail = {**detail, "kind": summary["kind"]}
        run = by_job.get(worker_id)
        if run is None:
            run = run_from_worker(detail, store)
            runs.save(run)
            by_job[worker_id] = run
        else:
            refresh_run(run)
            runs.save(run)


def register_cleanup(run_id: str, paths: list[Path]) -> None:
    if paths:
        with _lock:
            _cleanup_by_run[run_id] = list(paths)


def _finish_cleanup(run_id: str) -> None:
    with _lock:
        paths = _cleanup_by_run.pop(run_id, [])
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


def forget_worker(worker_id: str) -> None:
    with _lock:
        _dropped.add(worker_id)


def reset_follow_state() -> None:
    with _lock:
        _dropped.clear()
        _cleanup_by_run.clear()
