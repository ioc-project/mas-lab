#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Benchmark export/import commands."""

import hashlib
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from mas.lab.benchmark.cli.common import _get_trace_cache_dir, _resolve_run_manager_dir
from mas.lab.benchmark.run_manager import BenchmarkRunManager

logger = logging.getLogger(__name__)

_YAML_SUFFIXES = {".yaml", ".yml"}
_GENERIC_RUN_DIR_NAMES = {"results", "output", "outputs", "benchmark", "benchmarks"}
_HF_TOKEN_ENV = "HF_TOKEN"


def _find_workspace_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return None


def _slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return text or "benchmark"


def _archive_source_path(path: Path, workspace_root: Path | None) -> Path:
    resolved = path.resolve()
    if workspace_root is not None:
        try:
            return Path("source") / resolved.relative_to(workspace_root)
        except ValueError:
            pass
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:10]
    return Path("source") / "external" / f"{digest}-{resolved.name}"


def _maybe_resolve_path(raw: str, base_dir: Path) -> Path | None:
    text = (raw or "").strip()
    if not text:
        return None
    if "\n" in text or "\r" in text:
        return None
    if len(text) > 240:
        return None
    if text.startswith(("env:", "http://", "https://", "library:", "pkg:", "package:")):
        return None
    if "{" in text or "}" in text:
        return None
    if not (
        text.startswith((".", "~", "/"))
        or any(
            text.endswith(suffix)
            for suffix in (".yaml", ".yml", ".json", ".csv", ".md")
        )
        or "/" in text
        or "\\" in text
    ):
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        return candidate if candidate.exists() else None
    except OSError:
        return None


def _iter_string_fields(node: Any) -> list[tuple[str | None, str]]:
    out: list[tuple[str | None, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                out.append((str(key), value))
            else:
                out.extend(_iter_string_fields(value))
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                out.append((None, value))
            else:
                out.extend(_iter_string_fields(value))
    return out


def _collect_source_bundle(experiment_yaml: Path) -> dict[str, Any]:
    import yaml as _yaml

    workspace_root = _find_workspace_root(experiment_yaml)
    source_entries: dict[str, dict[str, Any]] = {}
    pending_yaml: list[Path] = [experiment_yaml.resolve()]
    seen_yaml: set[Path] = set()

    def _add_entry(path: Path, *, kind: str | None = None) -> None:
        resolved = path.resolve()
        entry_kind = kind or ("dir" if resolved.is_dir() else "file")
        if entry_kind == "app_root" and resolved.is_file():
            resolved = resolved.parent
            entry_kind = "dir"
        arcname = _archive_source_path(resolved, workspace_root).as_posix()
        source_entries[arcname] = {
            "original_path": str(resolved),
            "archive_path": arcname,
            "kind": entry_kind,
        }

    _add_entry(experiment_yaml.parent, kind="dir")

    while pending_yaml:
        yaml_path = pending_yaml.pop()
        if yaml_path in seen_yaml or not yaml_path.exists():
            continue
        seen_yaml.add(yaml_path)
        try:
            doc = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.debug("suppressed", exc_info=True)
            continue

        for key, raw in _iter_string_fields(doc):
            resolved = _maybe_resolve_path(raw, yaml_path.parent)
            if resolved is None:
                continue
            if key == "manifest":
                _add_entry(resolved.parent, kind="app_root")
            elif resolved.is_dir() or key == "configs_dir":
                _add_entry(resolved, kind="dir")
            else:
                _add_entry(resolved, kind="file")
            if resolved.is_file() and resolved.suffix.lower() in _YAML_SUFFIXES:
                pending_yaml.append(resolved)

    experiment_arc = _archive_source_path(
        experiment_yaml.resolve(), workspace_root
    ).as_posix()
    return {
        "workspace_root": str(workspace_root) if workspace_root is not None else None,
        "experiment_yaml_path": str(experiment_yaml.resolve()),
        "experiment_yaml_archive_path": experiment_arc,
        "entries": sorted(
            source_entries.values(), key=lambda item: item["archive_path"]
        ),
    }


def _default_import_dirname(
    run_dir_basename: str, short_id: str, experiment_name: str
) -> str:
    if run_dir_basename.lower() not in _GENERIC_RUN_DIR_NAMES:
        return run_dir_basename
    return f"{short_id}-{_slugify(experiment_name)}"


def _load_manifest_from_tarball(tarball: Path) -> dict[str, Any]:
    import json as _json
    import tarfile

    with tarfile.open(tarball, "r:gz") as tar:
        try:
            mf_member = tar.getmember("MANIFEST.json")
        except KeyError as exc:
            raise ValueError(
                "Archive is missing MANIFEST.json — was it created by 'benchmark export'?"
            ) from exc
        extracted = tar.extractfile(mf_member)
        if extracted is None:
            raise ValueError("Archive MANIFEST.json could not be read.")
        return _json.loads(extracted.read())


def _default_hf_path_from_manifest(manifest: dict[str, Any]) -> str:
    experiment = _slugify(str(manifest.get("experiment_name") or "benchmark"))
    short_id = _slugify(str(manifest.get("short_id") or "run"))
    return f"benchmarks/{experiment}/{short_id}/bundle.tar.gz"


def _require_hf_token() -> str:
    import os

    token = os.environ.get(_HF_TOKEN_ENV, "").strip()
    if not token:
        raise ValueError(f"{_HF_TOKEN_ENV} is not set.")
    return token


def publish_hf_command(args) -> int:
    """Upload a benchmark archive tarball to a Hugging Face dataset repository.

    This is intentionally a thin transport layer over ``benchmark export``:
    the tarball remains the canonical reproducibility artifact, and HF stores
    that artifact without repacking or changing its contents.
    """
    from huggingface_hub import HfApi

    tarball = Path(args.tarball).expanduser().resolve()
    if not tarball.exists():
        logger.error("Archive not found: %s", tarball)
        return 1

    repo_id = str(args.repo_id).strip()
    if not repo_id:
        logger.error("--repo-id is required")
        return 1

    try:
        manifest = _load_manifest_from_tarball(tarball)
        token = _require_hf_token()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    path_in_repo = str(
        getattr(args, "path_in_repo", "") or ""
    ).strip() or _default_hf_path_from_manifest(manifest)

    print(f"\nPublishing benchmark archive {tarball.name}")
    print(f"  Repo:   {repo_id}")
    print(f"  Path:   {path_in_repo}")

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)
    api.upload_file(
        path_or_fileobj=str(tarball),
        path_in_repo=path_in_repo,
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Upload benchmark archive {manifest.get('short_id', tarball.stem)}",
    )

    print(
        f"\n✓ Uploaded to https://huggingface.co/datasets/{repo_id}/tree/main/{path_in_repo.rsplit('/', 1)[0]}"
    )
    return 0


def import_hf_command(args) -> int:
    """Download a benchmark archive from Hugging Face and restore it locally.

    The downloaded artifact is passed straight into ``import_command`` so the
    existing restore logic remains the single source of truth.
    """
    from types import SimpleNamespace

    from huggingface_hub import hf_hub_download

    repo_id = str(args.repo_id).strip()
    path_in_repo = str(args.path_in_repo).strip()
    if not repo_id:
        logger.error("--repo-id is required")
        return 1
    if not path_in_repo:
        logger.error("PATH_IN_REPO is required")
        return 1

    try:
        token = _require_hf_token()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    download_root = Path(tempfile.mkdtemp(prefix="mas-lab-hf-import-"))
    print(f"\nDownloading benchmark archive")
    print(f"  Repo:   {repo_id}")
    print(f"  Path:   {path_in_repo}")
    print(f"  Cache:  {download_root}")

    tarball = Path(
        hf_hub_download(
            repo_id=repo_id,
            repo_type="dataset",
            filename=path_in_repo,
            token=token,
            local_dir=str(download_root),
        )
    ).resolve()

    inner_args = SimpleNamespace(
        tarball=tarball,
        output_dir=getattr(args, "output_dir", None),
        trace_cache_dir=getattr(args, "trace_cache_dir", None),
        dry_run=getattr(args, "dry_run", False),
    )
    return import_command(inner_args)


def export_command(args) -> int:
    """Pack a benchmark run into a portable .tar.gz archive.

    The archive layout::

        source/                            ← minimal rerun inputs recovered from experiment YAML
            experiments/...                ← experiment directory (pipelines, local YAMLs)
            datasets/...                   ← referenced dataset YAMLs
            apps/...                       ← referenced MAS app tree(s)
        <benchmark_id>/                    ← benchmark output dir
            metadata.yaml
            results.csv
            plots/
            <scenario>/item<n>/r<n>/
                traces/events.jsonl
                traces/otel_spans.jsonl    (if present)
                ...
        trace-cache/                       ← referenced trace-cache entries
            <run_hash>/
                events.jsonl
                run_info.json
                ...
        MANIFEST.json                      ← import metadata

    The source bundle is intentionally minimal: it only includes the local
    experiment inputs needed to understand or re-run the benchmark later,
    while the benchmark output + trace-cache preserve the actual observed run.
    """
    import json as _json
    import tarfile

    benchmark_id = args.benchmark_id
    include_trace_cache = getattr(args, "include_trace_cache", True)
    dry_run = getattr(args, "dry_run", False)

    run_manager = BenchmarkRunManager(
        benchmarks_root=_resolve_run_manager_dir(getattr(args, "output_dir", None))
    )

    # Handle "last" / "latest" as alias for most-recent run
    result = None
    if benchmark_id in ("last", "latest"):
        result = run_manager.get_last_run()
        if not result:
            logger.error("No benchmark runs found.")
            return 1
        benchmark_id = result[0].benchmark_id

    if result is None:
        result = run_manager.get_run(benchmark_id)
        if not result:
            # Fallback: match by experiment_name
            for run_info in run_manager.list_runs():
                if run_info.experiment_name == benchmark_id:
                    result = run_manager.get_run(run_info.benchmark_id)
                    break
    if not result:
        logger.error("Benchmark not found: %s", benchmark_id)
        return 1

    metadata, run_dir = result
    run_dir = run_dir.resolve()

    # Determine output path
    out_path = getattr(args, "output", None)
    if out_path is None:
        out_path = Path.cwd() / f"{metadata.short_id}.tar.gz"
    out_path = Path(out_path).expanduser().resolve()

    print(f"\nExporting benchmark {metadata.short_id} → {out_path}")
    print(f"  Run dir:  {run_dir}")

    source_bundle = _collect_source_bundle(Path(metadata.experiment_yaml_path))
    source_entries = source_bundle["entries"]
    print(f"  Source entries: {len(source_entries)}")

    # Collect trace-cache entries referenced in results.csv / metadata
    trace_dirs: list[Path] = []
    if include_trace_cache:
        explicit_tc = getattr(args, "trace_cache_dir", None)
        tc_root = _get_trace_cache_dir(explicit=explicit_tc)
        results_csv = run_dir / "results.csv"
        if results_csv.exists():
            import csv as _csv

            with open(results_csv) as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    trace_path_str = row.get("trace_path", "")
                    if trace_path_str:
                        tp = Path(trace_path_str)
                        # tc_entry = the direct child of tc_root that holds this trace
                        # e.g. tc_root/<hash>/traces/events.jsonl → tc_root/<hash>
                        try:
                            tc_entry = (
                                tc_root
                                / Path(trace_path_str).relative_to(tc_root).parts[0]
                            )
                        except (ValueError, IndexError):
                            # Fallback: walk up until a direct child of tc_root
                            tc_entry = tp.parent
                            while (
                                tc_entry.parent != tc_root
                                and tc_entry.parent != tc_entry
                            ):
                                tc_entry = tc_entry.parent
                        if tc_entry.exists() and tc_entry.parent == tc_root:
                            trace_dirs.append(tc_entry.resolve())
        trace_dirs = list({str(d): d for d in trace_dirs}.values())  # deduplicate
        print(f"  Trace-cache entries: {len(trace_dirs)}")

    # Count total run_info.json entries in the benchmark dir (= expected run count)
    total_runs = sum(1 for _ in run_dir.rglob("run_info.json"))

    # Build MANIFEST
    manifest = {
        "benchmark_id": metadata.benchmark_id,
        "short_id": metadata.short_id,
        "experiment_name": metadata.experiment_name,
        "experiment_yaml_path": metadata.experiment_yaml_path,
        "experiment_yaml_archive_path": source_bundle["experiment_yaml_archive_path"],
        "run_dir_basename": run_dir.name,
        "has_trace_cache": bool(trace_dirs),
        "total_runs": total_runs,
        "trace_cache_entry_names": [d.name for d in trace_dirs],
        "source_entries": source_entries,
    }

    if dry_run:
        print("\n  [dry-run — nothing written]")
        print(f"  Would create: {out_path}")
        print(f"  Benchmark dir: {run_dir}")
        for entry in source_entries:
            print(f"  Source:       {entry['original_path']}")
        for td in trace_dirs:
            print(f"  Trace-cache:  {td}")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as mf:
        _json.dump(manifest, mf, indent=2)
        manifest_path = Path(mf.name)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out_path, "w:gz") as tar:
            # Add MANIFEST.json at archive root
            tar.add(manifest_path, arcname="MANIFEST.json")
            # Add benchmark run dir
            tar.add(run_dir, arcname=run_dir.name)
            # Add reproducibility source inputs
            for entry in source_entries:
                tar.add(entry["original_path"], arcname=entry["archive_path"])
            # Add trace-cache entries
            for tc_dir in trace_dirs:
                tar.add(tc_dir, arcname=f"trace-cache/{tc_dir.name}")
    finally:
        manifest_path.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / 1_048_576
    print(f"\n✓ Exported {size_mb:.1f} MB → {out_path}")
    print(f"\nTo restore:\n  mas-lab benchmark import {out_path}")
    print(
        f"\nTo restore to a temp directory:\n  mas-lab benchmark import {out_path} \\"
    )
    print(f"      --output-dir /tmp/test-bench --trace-cache /tmp/test-trace-cache")
    return 0


def import_command(args) -> int:
    """Restore a benchmark archive produced by ``benchmark export``.

    Restores three kinds of content:

    - the benchmark output directory itself (results, run_info, plots, per-run traces)
    - the referenced trace-cache entries needed for replay / inspection
    - the bundled experiment source inputs under ``<output_dir>/_repro/source``

    During import, absolute paths in ``metadata.yaml`` are patched so the
    restored benchmark points at the imported output directory. When a bundled
    experiment YAML is present, ``experiment_yaml_path`` is redirected to that
    restored copy rather than the original machine-local source path.
    """
    import tarfile

    tarball = Path(args.tarball).expanduser().resolve()
    dry_run = getattr(args, "dry_run", False)

    if not tarball.exists():
        logger.error("Archive not found: %s", tarball)
        return 1

    try:
        manifest = _load_manifest_from_tarball(tarball)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    short_id = manifest["short_id"]
    run_dir_basename = manifest["run_dir_basename"]
    has_trace_cache = manifest.get("has_trace_cache", False)
    total_runs = manifest.get(
        "total_runs", len(manifest.get("trace_cache_entry_names", []))
    )
    tc_entry_names: list[str] = manifest.get("trace_cache_entry_names", [])
    experiment_name = manifest.get("experiment_name", short_id)
    experiment_yaml_archive_path = manifest.get("experiment_yaml_archive_path")
    source_entries: list[dict[str, Any]] = manifest.get("source_entries", [])

    # Resolve target directories
    output_dir_arg = getattr(args, "output_dir", None)
    trace_cache_arg = getattr(args, "trace_cache_dir", None)

    if output_dir_arg:
        bench_target = Path(output_dir_arg).expanduser().resolve()
    else:
        from mas.lab import paths as _paths

        bench_target = (
            _paths.benchmark_root()
            / _default_import_dirname(run_dir_basename, short_id, experiment_name)
        ).resolve()

    trace_cache_target = _get_trace_cache_dir(explicit=trace_cache_arg)
    source_target = bench_target / "_repro"

    # Determine the data root for the demo hint
    from mas.lab import paths as _paths

    data_root = _paths.data_root()

    # Count cached entries BEFORE import
    cached_before = sum(
        1 for name in tc_entry_names if (trace_cache_target / name).exists()
    )

    print(f"\nImporting benchmark {short_id} from {tarball.name}")
    print(f"  Benchmark dir:  {bench_target}")
    if source_entries:
        print(f"  Source bundle:  {source_target}")
    if has_trace_cache:
        print(f"  Trace-cache:    {trace_cache_target}")
    print()
    print(
        f"  Before import — cache coverage: {cached_before}/{total_runs} runs available"
    )
    if cached_before == 0 and total_runs > 0:
        print(f"  (to verify from a fresh environment, run first:)")
        print(f"    export {_paths.MAS_DATA_ROOT_ENV}={data_root}")
        print(f"    mas-lab benchmark show {short_id}")
    print()

    if dry_run:
        with tarfile.open(tarball, "r:gz") as tar:
            members = [
                m.name
                for m in tar.getmembers()
                if not m.name.endswith(".json") or m.name == "MANIFEST.json"
            ]
            print(f"  [dry-run — nothing written]")
            print(f"  Archive contains {len(members)} entries")
        return 0

    bench_target.mkdir(parents=True, exist_ok=True)
    trace_cache_target.mkdir(parents=True, exist_ok=True)
    source_target.mkdir(parents=True, exist_ok=True)

    # Extract archive, routing entries to correct destinations
    with tarfile.open(tarball, "r:gz") as tar:
        # Build a quick lookup for trace-cache member names (for hardlink resolution)
        tc_members_in_archive: set[str] = {
            m.name for m in tar.getmembers() if m.name.startswith("trace-cache/")
        }
        for member in tar.getmembers():
            name = member.name
            if name == "MANIFEST.json":
                continue
            if name.startswith("trace-cache/"):
                rel = name[len("trace-cache/") :]
                dest = trace_cache_target / rel
            elif name.startswith("source/"):
                rel = name[len("source/") :]
                dest = source_target / "source" / rel
            elif name.startswith(run_dir_basename + "/") or name == run_dir_basename:
                rel = name[len(run_dir_basename) :].lstrip("/")
                dest = bench_target / rel if rel else bench_target
            else:
                # Unexpected entry — skip
                continue

            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif member.islnk() or member.issym():
                # The run_dir may contain hardlinks/symlinks pointing to trace-cache
                # entries on the original machine. Rewrite them to the new trace-cache
                # location or extract the referenced content from the archive.
                linkname = member.linkname
                # Strip the run_dir_basename prefix if present (tar hardlink format)
                if linkname.startswith(run_dir_basename + "/"):
                    abs_part = linkname[len(run_dir_basename) + 1 :]
                else:
                    abs_part = linkname
                # Find "trace-cache/<hash>/<file>" suffix in the link target
                tc_marker = "trace-cache/"
                idx = abs_part.rfind(tc_marker)
                if idx >= 0:
                    tc_rel = abs_part[
                        idx + len(tc_marker) :
                    ]  # e.g. "HASH/events.jsonl"
                    archive_tc_name = f"trace-cache/{tc_rel}"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if archive_tc_name in tc_members_in_archive:
                        # Copy content from the archived trace-cache entry
                        # Unlink dangling symlinks first (e.g. after trace-cache wipe)
                        if dest.is_symlink():
                            dest.unlink()
                        fobj = tar.extractfile(archive_tc_name)
                        if fobj is not None:
                            dest.write_bytes(fobj.read())
                    else:
                        # Rewrite as symlink to new trace-cache location
                        new_target = str(trace_cache_target / tc_rel)
                        if dest.is_symlink() or dest.exists():
                            dest.unlink(missing_ok=True)
                        dest.symlink_to(new_target)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                fobj = tar.extractfile(member)
                if fobj is not None:
                    dest.write_bytes(fobj.read())

    # Patch absolute paths in metadata.yaml
    meta_path = bench_target / "metadata.yaml"
    if meta_path.exists():
        import yaml as _yaml

        raw = _yaml.safe_load(meta_path.read_text()) or {}
        old_run_dir = raw.get("run_dir", "")
        if old_run_dir and old_run_dir != str(bench_target):
            old_prefix = old_run_dir.rstrip("/")
            new_prefix = str(bench_target).rstrip("/")

            def _repatch(v: str) -> str:
                if v and v.startswith(old_prefix):
                    return new_prefix + v[len(old_prefix) :]
                return v

            for field in ("run_dir", "results_file", "plots_dir"):
                if field in raw:
                    raw[field] = _repatch(raw[field])
            if experiment_yaml_archive_path:
                restored_experiment_yaml = source_target / experiment_yaml_archive_path
                raw["experiment_yaml_path"] = str(restored_experiment_yaml)
            elif "experiment_yaml_path" in raw:
                raw["experiment_yaml_path"] = _repatch(raw["experiment_yaml_path"])
            meta_path.write_text(_yaml.dump(raw, default_flow_style=False))
            logger.debug("Patched metadata.yaml: %s → %s", old_prefix, new_prefix)

    # Count cached entries AFTER import
    cached_after = sum(
        1 for name in tc_entry_names if (trace_cache_target / name).exists()
    )

    print(f"✓ Imported benchmark {short_id} → {bench_target}")
    print(
        f"  After import  — cache coverage: {cached_after}/{total_runs} runs available"
    )
    print()
    print(f"  mas-lab benchmark show {short_id}")
    return 0
