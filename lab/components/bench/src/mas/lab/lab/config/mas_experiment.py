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
    ExperimentDesignSpec,
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
    """How the bench walks the design matrix (parallelism, ordering, runner)."""

    design: ExperimentDesignSpec = field(default_factory=ExperimentDesignSpec)
    """What conditions are compared (mode, couplings, cartesian guard)."""

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

    checkpoints: list[CheckpointAxisEntry] = field(
        default_factory=lambda: [CheckpointAxisEntry(id="none")]
    )
    checkpoints_explicit: bool = False

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
    def from_yaml(
        cls, path: Path, *, model_override: Optional[str] = None
    ) -> "MASExperimentConfig":
        """Load a MASExperimentConfig from an experiment YAML file."""
        data, _manifest_version = load_experiment_data(path)
        return cls.from_data(data, path, model_override=model_override)

    @classmethod
    def from_data(
        cls, data: dict, path: Path, *, model_override: Optional[str] = None
    ) -> "MASExperimentConfig":
        """Construct from an already-loaded dict (e.g. after CLI merges).

        Accepts the same ``data`` shape that :func:`load_experiment_data`
        returns — a raw YAML dict that may have been modified in-memory
        before construction (e.g. ``merge_pipeline_attachments``).

        Dual-read: a top-level ``execution:`` mapping still loads, with a
        structured deprecation warning, and is mapped onto ``schedule`` /
        ``design`` / ``bench_emulation``. New keys win when both are present.
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

        base = cls._load_base_fields(
            exp_data, base_dir, yaml_path=path, model_override=model_override
        )

        legacy = exp_data.get("execution")
        if legacy:
            warn_deprecated("experiment.execution", where=str(path))

        mapped = split_legacy_execution(legacy) if isinstance(legacy, dict) else {}

        schedule_src = dict(mapped.get("schedule") or {})
        if "schedule" in exp_data and isinstance(exp_data["schedule"], dict):
            schedule_src.update(exp_data["schedule"])
        schedule = ExperimentScheduleSpec.from_dict(schedule_src)

        design_src = dict(mapped.get("design") or {})
        if "design" in exp_data and isinstance(exp_data["design"], dict):
            design_src.update(exp_data["design"])
        design = ExperimentDesignSpec.from_dict(design_src)

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
            bench_emulation=bench_emulation,
            replay=replay,
            n_runs=n_runs,
        )

        default_flavour = exp_data.get("default_flavour") or "local"
        default_infra = exp_data.get("default_infra") or None
        checkpoints_explicit = "checkpoints" in exp_data
        checkpoints = _parse_checkpoint_axis(exp_data.get("checkpoints"), base_dir)

        config = cls(
            **base,
            schedule=schedule,
            design=design,
            bench_emulation=bench_emulation,
            replay=replay,
            execution=execution,
            default_flavour=default_flavour,
            default_infra=default_infra,
            checkpoints=checkpoints,
            checkpoints_explicit=checkpoints_explicit,
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
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for char in checkpoint_id
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
