#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""PlannedRun completeness and pipeline stage modes.

A run is **complete** only when the executor says the invoke finished as
intended. An error, a timeout, a lost worker, or a hidden-in-output
failure is **incomplete**. The host marks that run ``error`` and may
schedule it again.

A pipeline **stage** declares how it reads runs:

- ``batch`` — complete runs only; incomplete runs are skipped.
- ``live`` — events as they arrive, flagged live and possibly incomplete.
  A real-time telemetry pipeline cannot wait or re-run.
- ``mixed`` — live fold plus a completeness gate before any batch persist.

A live stage feeds a batch stage only through that gate.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

STAGE_BATCH = "batch"
STAGE_LIVE = "live"
STAGE_MIXED = "mixed"
STAGE_MODES = frozenset({STAGE_BATCH, STAGE_LIVE, STAGE_MIXED})

_DIGEST = re.compile(r"[0-9a-f]{8,64}")
"""A trace-cache entry name. A run folder from elsewhere must not name a path through it."""

COMPLETE_CHILD = frozenset({"ok", "cached"})

_run_complete: ContextVar[bool] = ContextVar("mas_lab_run_complete", default=True)


def child_is_complete(status: str | None) -> bool:
    """True when a child run status may enter a batch stage."""
    return str(status or "") in COMPLETE_CHILD


def current_run_complete() -> bool:
    """Completeness of the run the current pipeline node is bound to."""
    return _run_complete.get()


def bind_run_complete(complete: bool):
    """Bind the completeness of the run one scheduler node reads. Returns the reset token."""
    return _run_complete.set(bool(complete))


def reset_run_complete(token) -> None:
    _run_complete.reset(token)


def load_result_file(path: Path) -> dict | None:
    """The mapping in a ``result.json``; ``None`` when missing, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_result(run_dir: Path) -> dict | None:
    """A run folder's ``result.json``, or that of its trace-cache twin; ``None`` if neither exists."""
    root = Path(run_dir)
    local = load_result_file(root / "result.json")
    if local is not None:
        return local
    try:
        digest = (root / ".run_ref").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not _DIGEST.fullmatch(digest):
        return None
    from mas.lab.paths import trace_cache

    return load_result_file(trace_cache() / digest / "result.json")


def result_is_complete(result: dict | None) -> bool:
    return child_is_complete((result or {}).get("status"))


def result_file_is_complete(path: Path) -> bool:
    """True when ``result.json`` exists and records a complete status."""
    return result_is_complete(load_result_file(path))


def run_dir_is_complete(run_dir: Path | None) -> bool:
    """True when a run folder (or its trace-cache twin) finished complete."""
    return run_dir is not None and result_is_complete(read_result(run_dir))


def invoke_is_complete(*, status: str, error: str = "") -> bool:
    """Default executor completeness: only a non-error invoke is complete."""
    del error
    return str(status or "") != "error"


def normalize_stage_mode(
    mode: str | None,
    *,
    realtime: bool | None = None,
    folds: bool = False,
) -> str:
    """Resolve ``batch`` / ``live`` / ``mixed``. Explicit ``mode`` wins.

    When ``mode`` is omitted: ``realtime: true`` or a folding class is
    ``live``; ``realtime: false`` or execute-only is ``batch``.
    """
    if mode is not None and str(mode).strip():
        token = str(mode).strip().lower()
        if token not in STAGE_MODES:
            raise ValueError(f"stage mode must be batch, live, or mixed, got {mode!r}")
        if realtime is True and token == STAGE_BATCH:
            raise ValueError("stage mode batch cannot be combined with realtime: true")
        if realtime is False and token == STAGE_LIVE:
            raise ValueError("stage mode live cannot be combined with realtime: false")
        return token
    if realtime is True:
        return STAGE_LIVE
    if realtime is False:
        return STAGE_BATCH
    return STAGE_LIVE if folds else STAGE_BATCH


def stage_mode_of(step: Any) -> str:
    """Stage mode declared on a pipeline step instance or spec."""
    from mas.lab.benchmark.pipeline.incremental import folds_batches

    mode = getattr(step, "mode", None) or None
    realtime = getattr(step, "realtime", None)
    return normalize_stage_mode(mode, realtime=realtime, folds=folds_batches(step))


def edge_rule(producer_mode: str, consumer_mode: str) -> str:
    """What a producer stage must satisfy to feed a consumer stage.

    ``any``: the consumer is live or mixed and takes whatever arrives. ``complete``: a batch
    consumer needs a complete run. ``completeness_gate``: the same, from a live producer,
    whose output only passes once the run is known complete.
    """
    if consumer_mode != STAGE_BATCH:
        return "any"
    return "completeness_gate" if producer_mode == STAGE_LIVE else "complete"
