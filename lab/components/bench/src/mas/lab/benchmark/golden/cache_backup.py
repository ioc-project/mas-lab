#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Backup trace-cache entries for golden-run comparison."""

import json
import shutil
from pathlib import Path
from typing import Optional


def find_events_in_tree(root: Path) -> list[Path]:
    """Return non-empty events.jsonl files under *root*."""
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("events.jsonl")
        if p.is_file() and p.stat().st_size > 0
    )


def _cache_entry_for_events(events_path: Path) -> Optional[Path]:
    """Return trace-cache run dir containing *events_path* if layout matches."""
    traces = events_path.parent
    if traces.name != "traces":
        return None
    run_dir = traces.parent
    if (run_dir / "run.json").is_file() or (run_dir / "inputs.json").is_file():
        return run_dir
    return None


def backup_trace_cache(
    trace_cache_dir: Path,
    backup_root: Path,
    *,
    label: str,
) -> Path:
    """Copy all cache entries from *trace_cache_dir* to *backup_root/label/*.

    Returns the backup directory path.
    """
    dest = backup_root / label
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"label": label, "entries": []}
    if trace_cache_dir.is_dir():
        for entry in sorted(trace_cache_dir.iterdir()):
            if not entry.is_dir():
                continue
            # EventStream writes a sibling ``streams/`` dir; only hashed run
            # entries belong in the golden cache backup.
            run_json = entry / "run.json"
            inputs_json = entry / "inputs.json"
            has_events = (entry / "traces" / "events.jsonl").is_file()
            if not run_json.is_file() and not inputs_json.is_file() and not has_events:
                continue
            target = dest / entry.name
            shutil.copytree(entry, target)
            manifest["entries"].append({
                "run_hash": entry.name,
                "has_run_json": run_json.is_file(),
                "has_inputs_json": inputs_json.is_file(),
                "has_events": has_events,
            })

    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


def backup_events_golden(
    events_path: Path,
    golden_dir: Path,
    *,
    also_cache_entry: Optional[Path] = None,
) -> Path:
    """Write events.jsonl and optional cache snapshot under *golden_dir*."""
    from mas.lab.benchmark.golden.events import normalize_events_file, write_normalized_events

    golden_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(events_path, golden_dir / "events.jsonl")
    normalized = normalize_events_file(events_path)
    write_normalized_events(normalized, golden_dir / "events.normalized.jsonl")

    cache_entry = also_cache_entry or _cache_entry_for_events(events_path)
    if cache_entry and cache_entry.is_dir():
        cache_dest = golden_dir / "cache-entry"
        if cache_dest.exists():
            shutil.rmtree(cache_dest)
        shutil.copytree(cache_entry, cache_dest)

    return golden_dir
