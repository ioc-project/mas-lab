#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifact_types import type_info


@dataclass
class PipelineStepSpec:
    """Specification for a single step in an inline experiment pipeline.

    Inline pipeline steps are declared inside the ``pipeline:`` section of
    an experiment YAML and are expanded / executed automatically after the
    benchmark run completes.

    **v1 format** — flat list with ``per_scenario: true``:

    .. code-block:: yaml

        pipeline:
          - name: extract
            type: extract_trajectories
            per_scenario: true

    **v2 format** — level-scoped with ``in:``/``out:`` artifact references:

    .. code-block:: yaml

        run:
          pipeline:
            - name: stats
              type: extract_trace_stats
              in: trace
              out: metrics

    Both formats are supported.  v2 is detected when the experiment YAML
    contains ``run:``, ``test:``, or ``scenario:`` level sections.
    """

    type: str
    """Step type key (e.g. ``eval_mce``, ``processor``, ``extract_trajectories``)."""

    name: Optional[str] = None
    """Optional explicit step name.  Auto-generated when absent."""

    per_scenario: bool = False
    """(v1) When True, expand this step once for each scenario."""

    per_run: bool = False
    """(v1) When True, expand this step once for each benchmark run folder."""

    concurrency: int = 1
    """Scheduler cap for this step type when > 1 (per-type semaphore)."""

    phase: str = "post"
    """Execution phase: ``pre`` (before benchmark loop) or ``post`` (after)."""

    scope: str = ""
    """Execution scope assigned by the level section (``run``, ``test``,
    ``scenario``, ``experiment``).  In v1, set explicitly or inferred
    from ``per_scenario``."""

    config: Dict[str, Any] = field(default_factory=dict)
    """Step-type-specific configuration (merged with injected template vars)."""

    depends_on: List[str] = field(default_factory=list)
    """Names of steps that must complete before this step runs."""

    inputs: List[str] = field(default_factory=list)
    """(v2) Input artifact names.  When a step at level L references an
    artifact from level L-1, it fans in all instances."""

    outputs: List[str] = field(default_factory=list)
    """(v2) Output artifact names.  Must match artifacts declared at the
    step's level."""

    role: str = ""
    resource: str = "cpu"
    refresh: str = ""
    batch: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineStepSpec":
        # Parse in/out — accept scalar or list
        raw_in = data.get("in", [])
        inputs = [raw_in] if isinstance(raw_in, str) else list(raw_in)
        raw_out = data.get("out", [])
        outputs = [raw_out] if isinstance(raw_out, str) else list(raw_out)
        from mas.lab.benchmark.pipeline.artifact_paths import (
            step_config_label,
            validate_step_config_paths,
        )

        step_label = step_config_label(data)
        return cls(
            type=data["type"],
            name=data.get("name"),
            per_scenario=data.get("per_scenario", False),
            per_run=data.get("per_run", False),
            concurrency=int(data.get("concurrency", 1)),
            phase=data.get("phase", "post"),
            scope=data.get("scope", ""),
            config=validate_step_config_paths(
                step_label, data.get("config", {}), step_type=str(data["type"])
            ),
            depends_on=data.get("depends_on", []),
            inputs=inputs,
            outputs=outputs,
            role=str(data.get("role") or ""),
            resource=str(data.get("resource") or "cpu"),
            refresh=str(data.get("refresh") or ""),
            batch=dict(data.get("batch") or {}),
        )


# ---------------------------------------------------------------------------
# ArtifactSpec — named data product at a hierarchy level
# ---------------------------------------------------------------------------


def _confined_artifact_template(name: str, path: str) -> str:
    """Validate a declared artifact ``path`` template; keep a leading ``{level_dir}`` etc."""
    from mas.lab.benchmark.pipeline.artifact_paths import normalize_relative_value

    head = ""
    for prefix in ("{run_dir}", "{level_dir}", "{scenario_dir}"):
        if path.startswith(prefix):
            head, path = prefix + "/", path[len(prefix):].lstrip("/\\")
            break
    return head + str(normalize_relative_value(f"artifact {name}", "path", path))


@dataclass
class ArtifactSpec:
    """A named, typed data product declared at a hierarchy level.

    Artifacts can be declared in short form (``name: type``) or long form:

    .. code-block:: yaml

        artifacts:
          metrics: metrics                    # short
          trajectory:                      # long
            type: plot
            path: "{run_dir}/trajectory.html"
            validate: true

    Types come from library.yaml plugins (``type: artifact``).  The
    execution engine writes ``trace`` and ``run_info`` at run level.
    """

    name: str
    """Artifact name (unique within the level)."""

    type: str
    """Type key from a library.yaml ``type: artifact`` plugin."""

    path: Optional[str] = None
    """Path template.  If None, uses the default for the type."""

    validate: bool = False
    """Whether to validate the artifact schema after creation."""

    @property
    def effective_path(self) -> str:
        """Return the path template, falling back to the type default."""
        return self.path or type_info(self.type).get("path", "{level_dir}/{name}")

    @property
    def format(self) -> str:
        """Return the file format for this artifact type."""
        return type_info(self.type).get("format", "")

    @property
    def description(self) -> str:
        """Return the description for this artifact type."""
        return type_info(self.type).get("description", "")

    def relative_path(self) -> Path:
        """Path relative to the producing level directory.

        A custom ``path`` template keeps subdirectories after ``{run_dir}`` /
        ``{level_dir}`` / ``{output_dir}``.  Short-form declarations use
        ``{name}.{format}`` — not the type's default filename — so two
        ``plot`` artifacts do not collapse onto ``plot.png``.
        """
        if self.path:
            parts = [
                p
                for p in Path(self.path).parts
                if not (p.startswith("{") and p.endswith("}"))
            ]
            return Path(*parts) if parts else Path(Path(self.path).name)
        fmt = self.format
        return Path(self.name if not fmt else f"{self.name}.{fmt}")

    @classmethod
    def from_entry(cls, name: str, value: Any) -> "ArtifactSpec":
        """Parse a single artifact entry from the YAML artifacts map.

        *value* is either a string (short form) or a dict (long form).
        """
        if isinstance(value, str):
            return cls(name=name, type=value)
        if isinstance(value, dict):
            path = value.get("path")
            if isinstance(path, str):
                path = _confined_artifact_template(name, path)
            return cls(
                name=name,
                type=value["type"],
                path=path,
                validate=value.get("validate", False),
            )
        raise ValueError(f"Invalid artifact spec for '{name}': expected str or dict, got {type(value)}")


# ---------------------------------------------------------------------------
# LevelSpec — artifacts + pipeline at a hierarchy level
# ---------------------------------------------------------------------------

def _pipeline_ref_raw(entry: dict) -> Optional[str]:
    """Return a library id / file path from ``{ref:}`` or ``{id:}`` wrappers."""
    if "type" in entry or "steps" in entry:
        return None
    raw = entry.get("ref")
    if raw is None:
        raw = entry.get("id")
    if raw is None or isinstance(raw, (dict, list)):
        return None
    text = str(raw).strip()
    return text or None


def _resolve_pipeline_ref(raw: str, base_dir: Optional[Path]) -> Path:
    """Resolve ``library:name``, ``{id:}``, or a filesystem path to a YAML file."""
    from mas.runtime.spec.source import resolve_yaml_path

    anchor = base_dir if base_dir is not None else Path(".")
    try:
        path = resolve_yaml_path(raw, anchor)
    except LookupError as exc:
        raise ValueError(f"pipeline not found: {raw}") from exc
    except FileNotFoundError:
        path = Path(raw)
        if not path.is_absolute() and base_dir is not None:
            path = (base_dir / path).resolve()
    if not path.is_file():
        raise ValueError(f"pipeline not found: {raw}")
    return path


def _expand_pipeline_entries(
    entries: Any,
    *,
    level: str,
    phase: str,
    base_dir: Optional[Path] = None,
) -> List[PipelineStepSpec]:
    if not entries:
        return []
    if isinstance(entries, str):
        entries = [{"ref": entries}]
    if not isinstance(entries, list):
        raise ValueError(f"{level}.{phase} must be a list or pipeline file path")
    steps: List[PipelineStepSpec] = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"ref": entry}
        raw_ref = _pipeline_ref_raw(entry) if isinstance(entry, dict) else None
        if raw_ref:
            from mas.lab.benchmark.schedule.pipeline_resolve import (
                resolve_pipeline_specs_from_yaml,
            )

            ref_path = _resolve_pipeline_ref(raw_ref, base_dir)
            for step in resolve_pipeline_specs_from_yaml(ref_path):
                if not step.scope:
                    step.scope = level
                if step.phase == "post" and phase == "pre":
                    step.phase = phase
                elif not step.phase:
                    step.phase = phase
                steps.append(step)
            continue
        if isinstance(entry, dict) and "steps" in entry:
            for step in entry["steps"]:
                steps.append(
                    PipelineStepSpec.from_dict({**step, "scope": level, "phase": phase})
                )
        elif isinstance(entry, dict) and "type" in entry:
            steps.append(
                PipelineStepSpec.from_dict({**entry, "scope": level, "phase": phase})
            )
        else:
            raise ValueError(
                f"{level}.{phase}: each entry must be a step object, "
                f"{{ref: path}}, {{id: library:name}}, {{steps: [...]}}, "
                f"or a pipeline file path string"
            )
    return steps


@dataclass
class LevelSpec:
    """Artifacts and pipeline declared at one level of the experiment hierarchy.

    Levels (innermost → outermost):
        ``run`` → ``test`` → ``scenario`` → ``application``
    """

    level: str
    """Level name: ``run``, ``test``, ``scenario``, or ``application``."""

    artifacts: List[ArtifactSpec] = field(default_factory=list)
    """Artifacts declared at this level."""

    pipeline: List[PipelineStepSpec] = field(default_factory=list)
    """Pipeline steps executed at this level (from ``pre`` / ``post``)."""

    n_runs: Optional[int] = None
    """Run count (``run`` level only)."""

    @classmethod
    def from_dict(
        cls,
        level: str,
        data: Dict[str, Any],
        *,
        base_dir: Optional[Path] = None,
    ) -> "LevelSpec":
        if "pipeline" in data:
            raise ValueError(
                f"experiment.{level}.pipeline is removed; use {level}.pre or {level}.post"
            )
        artifacts = [
            ArtifactSpec.from_entry(name, value)
            for name, value in data.get("artifacts", {}).items()
        ]
        pipeline: List[PipelineStepSpec] = []
        pipeline.extend(
            _expand_pipeline_entries(
                data.get("pre", []), level=level, phase="pre", base_dir=base_dir
            )
        )
        pipeline.extend(
            _expand_pipeline_entries(
                data.get("post", []), level=level, phase="post", base_dir=base_dir
            )
        )
        for step in pipeline:
            if not step.scope:
                step.scope = level
        n_runs = data.get("n_runs") if level == "run" else None
        return cls(level=level, artifacts=artifacts, pipeline=pipeline, n_runs=n_runs)

