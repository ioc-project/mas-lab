#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mas.lab.deprecations import warn_deprecated
from mas.lab.manifests import load_experiment_data

from .execution import (
    EmulationSpec,
    ExperimentAnalyzerSpec,
    ExperimentDesignSpec,
    ExperimentExecutorSpec,
    ExperimentRetrySpec,
    ExperimentScheduleSpec,
    MASExecutionSpec,
    ReplaySpec,
    split_legacy_execution,
)
from .experiment_base import MASRunBase, _is_mas_binding, canonicalize_experiment_dict


@dataclass(frozen=True)
class CheckpointAxisEntry:
    """One starting checkpoint crossed with scenarios and dataset items."""

    id: str
    path: Path | None = None


def _n_runs_from_levels(base: dict, fallback: int) -> int:
    run_level = base.get("levels", {}).get("run")
    if run_level is not None and run_level.n_runs is not None:
        return int(run_level.n_runs)
    return fallback


@dataclass
class MASExperimentConfig(MASRunBase):
    """Batch experiment configuration for running a MAS across scenarios."""

    schedule: ExperimentScheduleSpec = field(default_factory=ExperimentScheduleSpec)
    """How the bench walks the design matrix (ordering, parallelism, pacing)."""

    design: ExperimentDesignSpec = field(default_factory=ExperimentDesignSpec)
    """What conditions are compared (scenario generator plugin, cartesian guard)."""

    executor: ExperimentExecutorSpec = field(default_factory=ExperimentExecutorSpec)
    """How each run is invoked (retry, timeout, workers, application runner)."""

    retry: ExperimentRetrySpec = field(default_factory=ExperimentRetrySpec)
    """Whether a failed run runs again (retrier plugin)."""

    analyzer: ExperimentAnalyzerSpec = field(default_factory=ExperimentAnalyzerSpec)
    """Who reads the results tree (analyzer plugin)."""

    bench_emulation: EmulationSpec = field(default_factory=EmulationSpec)
    """Mock/replay/trace-cache posture held constant unless declared a factor."""

    replay: Optional[ReplaySpec] = None
    """Multi-turn replay configuration (dataset-level design)."""

    execution: MASExecutionSpec = field(default_factory=MASExecutionSpec)
    """Compatibility view of schedule + design + bench_emulation.

    Synthesized from the split blocks (and from a deprecated ``execution:``
    mapping during the dual-read window). Prefer the named fields above.
    """

    default_flavour: Optional[str] = "local"
    """Default flavour name (library-standard)."""

    default_infra: Optional[str] = None
    """Default infra bundle name for service/codec pipeline steps."""

    checkpoints: list[CheckpointAxisEntry] = field(default_factory=lambda: [CheckpointAxisEntry(id="none")])
    checkpoints_explicit: bool = False

    session_checkpoints: list[dict] = field(default_factory=list)
    """Named persist points compiled to environment checkpoint/pause triggers."""

    @property
    def n_runs(self) -> int:
        """Replication count — a design field, sourced from ``run.n_runs``."""
        run_level = self.levels.get("run")
        if run_level is not None and run_level.n_runs is not None:
            return int(run_level.n_runs)
        if self.execution and self.execution.n_runs:
            return int(self.execution.n_runs)
        return 1

    @classmethod
    def from_yaml(cls, path: Path, *, model_override: Optional[str] = None) -> "MASExperimentConfig":
        """Load a MASExperimentConfig from an experiment YAML file."""
        data, _manifest_version = load_experiment_data(path)
        return cls.from_data(data, path, model_override=model_override)

    @classmethod
    def from_data(cls, data: dict, path: Path, *, model_override: Optional[str] = None) -> "MASExperimentConfig":
        """Construct from an already-loaded dict (e.g. after CLI merges).

        Accepts the same ``data`` shape that :func:`load_experiment_data`
        returns — a raw YAML dict that may have been modified in-memory
        before construction (e.g. ``merge_pipeline_attachments``).

        Dual-read: a top-level ``execution:`` mapping still loads, with a
        structured deprecation warning, and is mapped onto ``schedule`` /
        ``design`` / ``executor`` / ``bench_emulation``. The same host key in
        leftover ``execution:`` and the new block is an error. Disjoint keys
        still merge.
        """
        exp_data = data.get("experiment", data)
        base_dir = path.parent

        canonicalize_experiment_dict(exp_data, path=path)
        if (
            not _is_mas_binding(exp_data.get("application"))
            and "applications" not in exp_data
            and not exp_data.get("mas")
        ):
            raise ValueError(
                f"{path}: experiment must declare application: "
                f"{{app|manifest, configs_dir}} "
                f"(applications: is deprecated)"
            )

        base = cls._load_base_fields(exp_data, base_dir, yaml_path=path, model_override=model_override)

        legacy = exp_data.get("execution")
        if legacy:
            warn_deprecated("experiment.execution", where=str(path))

        mapped = split_legacy_execution(legacy) if isinstance(legacy, dict) else {}

        from mas.lab.lab.config.normalize import (
            combine_executor_sources,
            exclusive_merge,
            peel_executor_keys,
            peel_retry_keys,
        )

        mapped_schedule = dict(mapped.get("schedule") or {})
        explicit_schedule = dict(exp_data["schedule"]) if isinstance(exp_data.get("schedule"), dict) else {}
        mapped_schedule, mapped_sched_exec = peel_executor_keys(mapped_schedule)
        explicit_schedule, peeled_executor = peel_executor_keys(explicit_schedule)
        explicit_schedule, peeled_retry = peel_retry_keys(explicit_schedule)
        schedule = ExperimentScheduleSpec.from_dict(
            exclusive_merge(mapped_schedule, explicit_schedule, what="execution and schedule"),
            where=str(path),
        )

        legacy_executor = exclusive_merge(
            dict(mapped.get("executor") or {}),
            exclusive_merge(mapped_sched_exec, peeled_executor, what="execution and schedule"),
            what="execution and schedule",
        )
        explicit_executor = dict(exp_data["executor"]) if isinstance(exp_data.get("executor"), dict) else {}
        executor = ExperimentExecutorSpec.from_dict(
            combine_executor_sources(peeled=legacy_executor, explicit=explicit_executor),
            where=str(path),
        )
        if peeled_executor or mapped_sched_exec or peeled_retry:
            warn_deprecated("experiment.schedule.executor", where=str(path))

        explicit_retry = dict(exp_data["retry"]) if isinstance(exp_data.get("retry"), dict) else {}
        legacy_retry = exclusive_merge(
            dict((mapped.get("retry") or {}).get("params") or {}), peeled_retry, what="execution and schedule"
        )
        retry = ExperimentRetrySpec.from_dict(
            {
                **explicit_retry,
                "params": exclusive_merge(
                    legacy_retry, dict(explicit_retry.get("params") or {}), what="legacy execution/schedule and retry"
                ),
            },
            where=str(path),
        )
        analyzer = ExperimentAnalyzerSpec.from_dict(
            exp_data["analyzer"] if isinstance(exp_data.get("analyzer"), dict) else None,
            where=str(path),
        )

        design = ExperimentDesignSpec.from_dict(
            exclusive_merge(
                dict(mapped.get("design") or {}),
                dict(exp_data["design"]) if isinstance(exp_data.get("design"), dict) else {},
                what="execution and design",
            ),
            where=str(path),
        )

        emu_src = dict(mapped.get("bench_emulation") or {})
        if "bench_emulation" in exp_data and isinstance(exp_data["bench_emulation"], dict):
            emu_src.update(exp_data["bench_emulation"])
        bench_emulation = EmulationSpec.from_dict(emu_src)

        replay_data = exp_data.get("replay") or mapped.get("replay")
        replay = ReplaySpec.from_dict(replay_data) if replay_data else None

        fallback_n_runs = 3
        if isinstance(legacy, dict) and "n_runs" in legacy:
            fallback_n_runs = int(legacy["n_runs"])
        n_runs = _n_runs_from_levels(base, fallback_n_runs)

        execution = MASExecutionSpec.from_split(
            schedule=schedule,
            design=design,
            executor=executor,
            bench_emulation=bench_emulation,
            replay=replay,
            n_runs=n_runs,
        )

        default_flavour = exp_data.get("default_flavour") or "local"
        default_infra = exp_data.get("default_infra") or None
        checkpoints_explicit = "checkpoints" in exp_data
        checkpoints = _parse_checkpoint_axis(exp_data.get("checkpoints"), base_dir)
        raw_checkpoints = exp_data.get("session_checkpoints") or []
        if not isinstance(raw_checkpoints, list) or not all(isinstance(item, dict) for item in raw_checkpoints):
            raise ValueError("session_checkpoints must be a list of mappings")
        session_checkpoints = [dict(item) for item in raw_checkpoints]

        config = cls(
            **base,
            schedule=schedule,
            design=design,
            executor=executor,
            retry=retry,
            analyzer=analyzer,
            bench_emulation=bench_emulation,
            replay=replay,
            execution=execution,
            default_flavour=default_flavour,
            default_infra=default_infra,
            checkpoints=checkpoints,
            checkpoints_explicit=checkpoints_explicit,
            session_checkpoints=session_checkpoints,
        )
        config._path = path
        return config


def _parse_checkpoint_axis(raw: object, base_dir: Path) -> list[CheckpointAxisEntry]:
    if raw is None:
        return [CheckpointAxisEntry(id="none")]
    if not isinstance(raw, list) or not raw:
        raise ValueError("experiment.checkpoints must be a non-empty list")
    entries: list[CheckpointAxisEntry] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, dict) or set(value) - {"id", "path"}:
            raise ValueError(f"experiment.checkpoints[{index}] must contain only id and path")
        checkpoint_id = value.get("id")
        if (
            not isinstance(checkpoint_id, str)
            or not checkpoint_id
            or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in checkpoint_id
            )
        ):
            raise ValueError(f"experiment.checkpoints[{index}].id must use letters, digits, '-' or '_'")
        if checkpoint_id in seen:
            raise ValueError(f"experiment.checkpoints contains duplicate id {checkpoint_id!r}")
        seen.add(checkpoint_id)
        raw_path = value.get("path")
        if checkpoint_id == "none":
            if raw_path is not None:
                raise ValueError("checkpoint id 'none' cannot declare a path")
            entries.append(CheckpointAxisEntry(id=checkpoint_id))
            continue
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"experiment.checkpoints[{index}].path is required")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        entries.append(CheckpointAxisEntry(id=checkpoint_id, path=path))
    return entries
