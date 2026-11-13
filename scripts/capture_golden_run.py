#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Capture golden-run artifacts (events.jsonl + trace-cache backup) for CI parity."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def _load_dotenv(root: Path) -> None:
    """Load ``root/.env`` into ``os.environ`` without overwriting existing vars.

    Direnv is not always active (e.g. when running inside tox, CI, or a bare
    Python subprocess).  Loading .env here ensures ``LLM_PROXY_API_BASE`` and
    similar vars are available regardless of the invoking shell.
    Lines starting with ``#`` and blank lines are skipped.  ``export`` prefix
    is stripped.  Existing env vars are never overwritten (same semantics as
    ``source_env_if_exists .env`` in direnv).
    """
    env_path = root / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export").strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val
            logger.debug("dotenv: loaded %s from .env", key)


def _capture_one(
    experiment: Path,
    label: str,
    output_root: Path,
) -> int:
    from mas.lab.benchmark.golden.cache_backup import (
        backup_events_golden,
        backup_trace_cache,
        find_events_in_tree,
    )
    from mas.lab.benchmark.golden.events import events_fingerprint, normalize_events_file
    from mas.lab.benchmark.worker import run_benchmark_sync

    if not experiment.is_file():
        print(f"skip {label}: missing {experiment}", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix=f"golden-{label}-"))
    out = tmp / "benchmark-out"
    trace_cache = tmp / "trace-cache"
    trace_cache.mkdir()
    mas_home = tmp / "mas-home"
    mas_home.mkdir()
    os.environ["MAS_HOME"] = str(mas_home)
    # Isolate trace cache: without this, run_benchmark_sync may hit a global
    # cache entry with stale event counts and return cached results instead of
    # running the agent.  The test fixture does the same via monkeypatch.
    os.environ["MAS_TRACE_CACHE"] = str(trace_cache)
    # Isolate XDG so the user's personal ~/.config/mas/config.yaml (which may
    # point at a private standard:llm-proxy) cannot bleed into the OSS capture.
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg-config")
    # Pin the workspace so find_workspace_file() always finds the OSS workspace
    # config.  Without this, the walk stops at
    # .git (no config.yaml at repo root), falls back to the now-isolated XDG
    # path, finds nothing, and resolve_infra_refs falls through to
    # standard:production — hitting a real LLM and causing a 401 or wrong event
    # count.  MAS_INFRA_REFS can still override this for real-LLM captures.
    if "MAS_WORKSPACE_ROOT" not in os.environ:
        os.environ["MAS_WORKSPACE_ROOT"] = str(ROOT / "library-samples" / "sample-workspace")
    # Same isolation as tests/test_golden_labs_run.py: replay the committed
    # llm_cache fixture, never the developer's llm_cache file or live provider.
    if "MAS_INFRA_REFS" not in os.environ:
        replay = ROOT / "tests/fixtures/llm-cache/ci-replay.yaml"
        os.environ["MAS_INFRA_REFS"] = f"standard:openai,{replay.resolve()}"
    os.environ["MAS_LLM_CACHE"] = str(tmp / "llm_cache.json")
    os.environ.setdefault("MAS_MCE_OFFLINE", "1")
    print(
        f"  workspace: MAS_WORKSPACE_ROOT={os.environ['MAS_WORKSPACE_ROOT']!r}",
        f"  infra:     MAS_INFRA_REFS={os.environ.get('MAS_INFRA_REFS', '<not set — workspace infra_refs used>')!r}",
        f"  api_base:  LLM_PROXY_API_BASE={os.environ.get('LLM_PROXY_API_BASE', '<not set>')!r}",
        sep="\n",
        file=sys.stderr,
    )

    ok = run_benchmark_sync(
        experiment,
        force=True,
        single_run=True,
        max_runs=1,
        output_dir=out,
        trace_cache_dir=trace_cache,
    )
    if not ok:
        print(f"benchmark run failed for {label}", file=sys.stderr)
        return 1

    events_paths = find_events_in_tree(out) or find_events_in_tree(trace_cache)
    if not events_paths:
        print(f"no events.jsonl found for {label}", file=sys.stderr)
        return 1

    events_path = events_paths[0]
    golden_dir = output_root / label
    backup_events_golden(events_path, golden_dir, also_cache_entry=None)
    backup_trace_cache(trace_cache, golden_dir / "cache-backup", label=label)

    fp = events_fingerprint(normalize_events_file(events_path))
    (golden_dir / "events.sha256").write_text(fp + "\n", encoding="utf-8")
    print(f"Captured golden run → {golden_dir}")
    print(f"  events: {events_path}")
    print(f"  fingerprint: {fp}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Load .env before argument parsing so env vars are available for infra resolution.
    _load_dotenv(ROOT)

    from mas.lab.benchmark.golden.labs import DEFAULT_MANIFEST, resolve_lab_targets

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=None,
        help="Single experiment YAML (default: lab-smoke fixture when no --labs)",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Golden fixture label (default: derived from experiment path)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "tests/fixtures/golden-runs",
    )
    parser.add_argument(
        "--labs-manifest",
        type=Path,
        default=ROOT / DEFAULT_MANIFEST,
        help="YAML manifest of lab labels → experiment paths",
    )
    parser.add_argument(
        "--labs",
        action="append",
        default=[],
        metavar="LAB",
        help=(
            "Lab to capture (repeatable): manifest label, path to experiment.yaml, "
            "path to *.lab directory, or 'all' for every entry in --labs-manifest"
        ),
    )
    args = parser.parse_args()

    if args.labs:
        try:
            targets = resolve_lab_targets(
                args.labs,
                root=ROOT,
                manifest_path=args.labs_manifest,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1
        if not targets:
            print("no lab targets resolved", file=sys.stderr)
            return 1
        rc = 0
        for label, experiment in targets:
            print(f"\n=== lab: {label} ===")
            if _capture_one(experiment, label, args.output_root) != 0:
                rc = 1
        return rc

    experiment = args.experiment or (ROOT / "tests/fixtures/lab-smoke/experiment.yaml")
    label = args.label
    if not label:
        from mas.lab.benchmark.golden.labs import default_label_for

        label = default_label_for(experiment.resolve(), root=ROOT)
    return _capture_one(experiment, label, args.output_root)


if __name__ == "__main__":
    raise SystemExit(main())
