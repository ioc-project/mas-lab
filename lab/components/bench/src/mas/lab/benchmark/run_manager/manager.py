#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""BenchmarkRunManager — stateful benchmark execution."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import click
from mas.lab import paths as _paths
from mas.lab.benchmark.metadata import BenchmarkMetadata, BenchmarkStatus
from mas.lab.benchmark.run_manager.discovery import default_search_roots, iter_metadata_locations
from mas.lab.benchmark.run_manager.display import format_run_details as _format_run_details
from mas.lab.benchmark.run_manager.display import format_run_summary as _format_run_summary
from mas.lab.benchmark.run_manager.models import BenchmarkRunInfo
from mas.lab.benchmark.run_manager.persistence import load_state as _load_state
from mas.lab.benchmark.run_manager.persistence import save_state as _save_state
from mas.lab.benchmark.run_manager.pointer import last_run_write_path, resolve_last_run_file
from mas.lab.benchmark.state import EnhancedBenchmarkState
from mas.runtime import hostenv

logger = logging.getLogger(__name__)

_NO_WRITABLE_ROOT = "no writable data directory: set MAS_LABS_ROOT or run from a writable workspace"


def _default_labs_root() -> Path:
    """The configured labs root; with no usable home, a directory under the working directory."""
    resolved = _paths.resolve_path("labs_dir")
    no_home = not hostenv.get("HOME", "").strip() and not hostenv.get("XDG_DATA_HOME", "").strip()
    if resolved.source == _paths.DEFAULT_PATH_SOURCE and no_home:
        return (Path.cwd() / ".mas" / "labs").resolve()
    return resolved.path


class BenchmarkRunManager:
    """Manages benchmark runs with persistent state."""

    def __init__(self, benchmarks_root: Optional[Path] = None):
        """Initialize run manager.

        Args:
            benchmarks_root: Primary output root (CLI ``-o``). When omitted, uses
                :func:`mas.lab.paths.labs_root` from the active config. Listing
                also scans :func:`mas.lab.paths.runs_root` when it differs.
        """
        if benchmarks_root is None:
            benchmarks_root = _default_labs_root()

        self.benchmarks_root = benchmarks_root
        self._search_roots = default_search_roots(extra=benchmarks_root)
        try:
            self.benchmarks_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise click.ClickException(_NO_WRITABLE_ROOT) from None

        logger.debug("Benchmark run manager initialized: roots=%s", self._search_roots)

    def _iter_run_dirs(self):
        """Yield ``(run_dir, metadata_path)`` from all configured search roots."""
        yield from iter_metadata_locations(*self._search_roots)

    def create_run(
        self,
        experiment_name: str,
        experiment_description: str,
        experiment_yaml_path: Path,
        total_scenarios: int,
    ) -> tuple[BenchmarkMetadata, EnhancedBenchmarkState, Path]:
        """Create a new benchmark run.

        Args:
            experiment_name: Name of experiment
            experiment_description: Description
            experiment_yaml_path: Path to experiment YAML
            total_scenarios: Total number of scenarios

        Returns:
            Tuple of (metadata, state, run_dir)
        """
        # Generate UUID and timestamp
        import uuid as uuid_lib

        benchmark_uuid = str(uuid_lib.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Create run directory with timestamp and UUID
        run_dir_name = f"{timestamp}_{benchmark_uuid[:8]}"
        run_dir = self.benchmarks_root / run_dir_name
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create metadata
        metadata = BenchmarkMetadata(
            benchmark_id=benchmark_uuid,
            timestamp=datetime.now().isoformat(),
            experiment_name=experiment_name,
            experiment_description=experiment_description,
            experiment_yaml_path=str(experiment_yaml_path),
            status=BenchmarkStatus.RUNNING,
            total_scenarios=total_scenarios,
            started_at=datetime.now().isoformat(),
            run_dir=str(run_dir),
            results_file=str(run_dir / "results.csv"),
            plots_dir=str(run_dir / "plots"),
        )

        # Save metadata
        metadata_path = run_dir / "metadata.yaml"
        metadata.to_yaml(metadata_path)

        # Create initial state (empty, caller will initialize scenarios)
        state = EnhancedBenchmarkState(
            benchmark_id=metadata.benchmark_id,
            total_scenarios=total_scenarios,
        )

        logger.info(f"Created benchmark run: {metadata.short_id} in {run_dir}")

        return metadata, state, run_dir

    def list_runs(
        self,
        status_filter: Optional[BenchmarkStatus] = None,
        limit: Optional[int] = None,
    ) -> List[BenchmarkRunInfo]:
        """List all benchmark runs.

        Args:
            status_filter: Filter by status (None = all)
            limit: Maximum number of runs to return (None = all)

        Returns:
            List of run info, sorted by timestamp (newest first)
        """
        runs = []
        locations = list(self._iter_run_dirs())

        # Sort by metadata timestamp (newest first), not directory name.
        parsed: list[tuple[datetime, BenchmarkMetadata, Path]] = []
        for run_dir, metadata_path in locations:
            try:
                metadata = BenchmarkMetadata.from_yaml(metadata_path)
                ts = metadata.timestamp
                if isinstance(ts, str):
                    ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    ts_dt = ts
                parsed.append((ts_dt, metadata, run_dir))
            except Exception as e:
                logger.warning("Failed to load metadata from %s: %s", metadata_path, e)

        parsed.sort(key=lambda row: row[0], reverse=True)

        for _ts, metadata, run_dir in parsed:
            if status_filter and metadata.status != status_filter:
                continue
            runs.append(BenchmarkRunInfo.from_metadata(metadata, run_dir))
            if limit and len(runs) >= limit:
                break

        return runs

    def get_run(self, benchmark_id: str) -> Optional[tuple[BenchmarkMetadata, Path]]:
        """Get a specific benchmark run.

        Args:
            benchmark_id: Full or short (8-char) benchmark ID

        Returns:
            Tuple of (metadata, run_dir) or None if not found
        """
        for run_dir, metadata_path in self._iter_run_dirs():
            try:
                metadata = BenchmarkMetadata.from_yaml(metadata_path)

                # Match full or short ID
                if metadata.benchmark_id == benchmark_id or metadata.short_id == benchmark_id:
                    return metadata, run_dir

            except Exception as e:
                logger.warning("Failed to load metadata from %s: %s", metadata_path, e)
                continue

        # Fallback: check last-run pointer (catches MAS runs outside benchmarks_root)
        return self._check_pointer_for_id(benchmark_id)

    def _check_pointer_for_id(self, benchmark_id: str) -> Optional[tuple[BenchmarkMetadata, Path]]:
        """Check the last-run pointer file for a matching benchmark ID (MAS runs live outside benchmarks_root)."""
        ptr_path = resolve_last_run_file()
        if ptr_path is None:
            return None
        try:
            ptr = json.loads(ptr_path.read_text())
            ptr_id = ptr.get("benchmark_id", "")
            if ptr_id == benchmark_id or ptr_id[:8] == benchmark_id:
                run_dir = Path(ptr["run_dir"])
                metadata_path = run_dir / "metadata.yaml"
                if metadata_path.exists():
                    meta = BenchmarkMetadata.from_yaml(metadata_path)
                    return meta, run_dir
        except Exception:
            logger.debug("suppressed", exc_info=True)
        return None

    def get_last_run(
        self,
        status_filter: Optional[BenchmarkStatus] = None,
    ) -> Optional[tuple[BenchmarkMetadata, Path]]:
        """Get the most recent benchmark run.

        Checks the global last-run pointer file first (updated by every run
        start, including MAS runs whose output_dir lives outside
        benchmarks_root), then falls back to scanning benchmarks_root.

        Args:
            status_filter: Filter by status (None = any)

        Returns:
            Tuple of (metadata, run_dir) or None if no runs found
        """
        # Try pointer file first — covers MAS experiments outside benchmarks_root
        ptr_path = resolve_last_run_file()
        if status_filter is None and ptr_path is not None:
            try:
                import json as _json

                ptr = _json.loads(ptr_path.read_text())
                run_dir = Path(ptr["run_dir"])
                metadata_path = run_dir / "metadata.yaml"
                if metadata_path.exists():
                    metadata = BenchmarkMetadata.from_yaml(metadata_path)
                    logger.debug("last-run pointer: %s", run_dir)
                    return metadata, run_dir
            except Exception as _e:
                logger.debug("Failed to read last-run pointer: %s", _e)

        # Fallback: scan benchmarks_root (single-agent runs)
        runs = self.list_runs(status_filter=status_filter, limit=1)
        if not runs:
            return None
        run_info = runs[0]
        return self.get_run(run_info.benchmark_id)

    def record_last_run(
        self,
        metadata: BenchmarkMetadata,
        run_dir: Path,
        *,
        experiment_yaml: Path | None = None,
    ) -> None:
        """Write a pointer to the most recently started run.

        Called at run start by both single-agent and MAS benchmark paths so
        ``mas-lab benchmark show last`` always resolves correctly.

        *experiment_yaml* overrides a reused metadata.yaml path when the
        same output dir is resumed with a different YAML (``-o``).
        """
        ts = metadata.timestamp
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        yaml_path = experiment_yaml if experiment_yaml is not None else metadata.experiment_yaml_path
        try:
            ptr_path = last_run_write_path()
            ptr_path.parent.mkdir(parents=True, exist_ok=True)
            ptr_path.write_text(
                json.dumps(
                    {
                        "run_dir": str(run_dir),
                        "benchmark_id": str(metadata.benchmark_id),
                        "experiment_name": str(metadata.experiment_name),
                        "experiment_yaml_path": str(Path(yaml_path).resolve()) if yaml_path else "",
                        "timestamp": str(ts),
                    }
                )
            )
        except Exception as _e:
            logger.error("Failed to write last-run pointer: %s", _e)

    def get_last_run_for_experiment(
        self,
        experiment_yaml_path: Path,
    ) -> Optional[tuple[BenchmarkMetadata, Path]]:
        """Find the most recent run that used a specific experiment YAML.

        Searches benchmarks_root by matching the canonical (resolved) path of
        the stored ``experiment_yaml_path``.  Used to implement the
        Makefile-like default: ``run`` without ``--force`` resumes the latest
        run for the same YAML.

        Args:
            experiment_yaml_path: Path to the experiment YAML to match.

        Returns:
            Tuple of (metadata, run_dir) or None if not found.
        """
        canonical = str(experiment_yaml_path.resolve())
        best: tuple[datetime, BenchmarkMetadata, Path] | None = None
        for run_dir, metadata_path in self._iter_run_dirs():
            try:
                metadata = BenchmarkMetadata.from_yaml(metadata_path)
                if str(Path(metadata.experiment_yaml_path).resolve()) != canonical:
                    continue
                ts = metadata.timestamp
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
                if best is None or ts_dt > best[0]:
                    best = (ts_dt, metadata, run_dir)
            except Exception:
                continue
        if best is None:
            return None
        return best[1], best[2]

    def load_state(self, run_dir: Path) -> Optional[EnhancedBenchmarkState]:
        """Load state from run directory.

        Returns ``None`` when ``state.json`` is absent.  Raises
        :class:`ValueError` for unsupported legacy formats.
        """
        return _load_state(run_dir)

    def save_state(self, run_dir: Path, state: EnhancedBenchmarkState) -> None:
        """Save state to run directory."""
        _save_state(run_dir, state)

    def save_metadata(self, run_dir: Path, metadata: BenchmarkMetadata) -> None:
        """Save metadata to run directory.

        Args:
            run_dir: Run directory
            metadata: Metadata to save
        """
        metadata_path = run_dir / "metadata.yaml"
        metadata.to_yaml(metadata_path)

    def update_metadata(
        self,
        benchmark_id: str,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        add_tags: Optional[List[str]] = None,
        remove_tags: Optional[List[str]] = None,
    ) -> bool:
        """Update benchmark metadata (name and/or tags).

        Args:
            benchmark_id: Benchmark ID (full or short)
            name: New name (None = no change)
            tags: Replace all tags with this list (None = no change)
            add_tags: Tags to add (None = no change)
            remove_tags: Tags to remove (None = no change)

        Returns:
            True if successful, False if benchmark not found
        """
        result = self.get_run(benchmark_id)
        if not result:
            logger.error(f"Benchmark not found: {benchmark_id}")
            return False

        metadata, run_dir = result

        # Update name
        if name is not None:
            metadata.name = name

        # Update tags
        if tags is not None:
            metadata.tags = tags
        elif add_tags or remove_tags:
            # Ensure tags is a list
            if metadata.tags is None:
                metadata.tags = []

            if add_tags:
                for tag in add_tags:
                    if tag not in metadata.tags:
                        metadata.tags.append(tag)

            if remove_tags:
                metadata.tags = [t for t in metadata.tags if t not in remove_tags]

        # Save updated metadata
        self.save_metadata(run_dir, metadata)
        logger.info(f"Updated benchmark {metadata.short_id}")

        return True

    def can_resume(self, benchmark_id: str) -> bool:
        """Check if a benchmark run can be resumed.

        A benchmark can be resumed if:
        - Status is INTERRUPTED, or
        - Status is RUNNING but the process is stale (not actively running)

        Args:
            benchmark_id: Benchmark ID

        Returns:
            True if can be resumed
        """
        result = self.get_run(benchmark_id)
        if not result:
            return False

        metadata, run_dir = result

        # Cannot resume completed or failed benchmarks
        if metadata.status not in [BenchmarkStatus.INTERRUPTED, BenchmarkStatus.RUNNING]:
            return False

        # If RUNNING, check if it's actually stale (process died)
        if metadata.status == BenchmarkStatus.RUNNING:
            if self.is_actively_running(benchmark_id):
                # Benchmark is actively running, cannot resume (it's already running!)
                return False

        # Must have state file
        state = self.load_state(run_dir)
        if not state:
            return False

        # Must have pending scenarios
        if state.pending_count == 0:
            return False

        return True

    def is_actively_running(self, benchmark_id: str) -> bool:
        """Check if a benchmark is actively running (has a live process).

        Checks multiple indicators:
        1. Benchmark lock file with active process
        2. Process PID in metadata (if available)

        Args:
            benchmark_id: Benchmark ID

        Returns:
            True if benchmark has an active process
        """
        result = self.get_run(benchmark_id)
        if not result:
            return False

        metadata, run_dir = result

        # Must be in RUNNING status
        if metadata.status != BenchmarkStatus.RUNNING:
            return False

        # Check lock file first (most reliable)
        lock_file = run_dir / ".benchmark.lock"
        if lock_file.exists():
            try:
                from mas.lab.benchmark.lock import BenchmarkLock

                lock = BenchmarkLock(run_dir)
                lock_info = lock.read_lock()
                if lock_info and lock_info.is_alive():
                    return True
            except Exception as e:
                logger.debug(f"Could not check lock file: {e}")

        # Fallback to checking process PID in metadata
        if metadata.process_pid:
            try:
                import psutil

                process = psutil.Process(metadata.process_pid)
                return process.is_running()
            except (ImportError, psutil.NoSuchProcess, psutil.AccessDenied):
                logger.debug("suppressed", exc_info=True)

        # No active indicators found
        return False

    def find_resumable_run(self) -> Optional[tuple[BenchmarkMetadata, EnhancedBenchmarkState, Path]]:
        """Find the most recent resumable run.

        Returns:
            Tuple of (metadata, state, run_dir) or None
        """
        # Look for interrupted or running (crashed) runs
        for status in [BenchmarkStatus.INTERRUPTED, BenchmarkStatus.RUNNING]:
            runs = self.list_runs(status_filter=status, limit=10)

            for run_info in runs:
                result = self.get_run(run_info.benchmark_id)
                if not result:
                    continue

                metadata, run_dir = result

                # Check if can resume
                if not self.can_resume(metadata.benchmark_id):
                    continue

                # Load state
                state = self.load_state(run_dir)
                if not state:
                    continue

                logger.info(f"Found resumable run: {metadata.short_id}")
                return metadata, state, run_dir

        return None

    def format_run_summary(self, run_info: BenchmarkRunInfo) -> str:
        """Format a run summary for display."""
        return _format_run_summary(run_info)

    def format_run_details(
        self,
        metadata: BenchmarkMetadata,
        run_dir: Path,
        verbose: bool = False,
        trace_cache_root: Optional[Path] = None,
    ) -> str:
        """Format run information."""
        return _format_run_details(
            metadata,
            run_dir,
            verbose=verbose,
            trace_cache_root=trace_cache_root,
        )
