#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.benchmark.experiment import EvaluationSpec
from mas.lab.deprecations import warn_deprecated
from mas.runtime.spec.model_ref import ANY_MODEL, normalize_model_slots
from mas.runtime.spec.source import resolve_path as resolve_path_ref

from .lab_context import _discover_lab_name
from .pipeline import ArtifactSpec, LevelSpec, PipelineStepSpec
from .scenario import MASScenarioSpec, MASSpec
from .scenario_loading import discover_scenario_stems

_DEPRECATED_EXPERIMENT_KEYS = {
    "pipeline_bind": "declare hooks under run/item/scenario/post",
    "pipeline": "use run/item/scenario/post hooks (CLI --depth exp|scenario|item|run)",
    "output_dir": "remove; output paths are derived from lab layout",
    "flavours": "use default_flavour (library-standard flavours)",
    "plots": "declare plot steps in experiment-level post: or scenario.post",
}

_LEVEL_SECTION_KEYS = frozenset({"pre", "post", "artifacts", "n_runs"})
_MAS_BINDING_KEYS = frozenset({"app", "manifest", "configs_dir", "base_scenario"})


def _is_mas_binding(value: Any) -> bool:
    return isinstance(value, dict) and bool(_MAS_BINDING_KEYS.intersection(value))


def _is_level_section(value: Any) -> bool:
    return isinstance(value, dict) and bool(_LEVEL_SECTION_KEYS.intersection(value)) and not _is_mas_binding(value)


def canonicalize_experiment_dict(
    data: Dict[str, Any],
    *,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Rewrite deprecated experiment keys in place to the CLI vocabulary.

    Canonical hierarchy (matches ``mas-lab benchmark show --depth``)::

        experiment → scenario → item → run

    Deprecated (still accepted, warned):
      - ``applications:`` list → ``application:`` object
      - ``application.post`` (pipeline level) → experiment-level ``post:``
      - ``test:`` → ``item:``
    """
    where = str(path) if path else "experiment"
    app = data.get("application")
    if _is_level_section(app):
        warn_deprecated("experiment.application_post", where=where)
        for phase in ("pre", "post"):
            incoming = list(app.get(phase) or [])
            if incoming:
                existing = data.get(phase)
                if isinstance(existing, list):
                    data[phase] = list(existing) + incoming
                else:
                    data[phase] = incoming
        if app.get("artifacts") and "artifacts" not in data:
            data["artifacts"] = app["artifacts"]
        del data["application"]
        app = None

    apps = data.get("applications")
    if apps and not _is_mas_binding(data.get("application")):
        warn_deprecated("experiment.applications", where=where)
        if isinstance(apps, list) and apps:
            data["application"] = apps[0]

    if "test" in data:
        warn_deprecated("experiment.test_key", where=where)
        item = data.get("item")
        test = data["test"]
        if item and test:
            raise ValueError(
                f"{path or 'experiment'}: declare item: or test:, not both"
            )
        data["item"] = test
        del data["test"]

    return data


def _reject_deprecated_experiment_keys(
    data: Dict[str, Any],
    *,
    path: Optional[Path],
    allow_output_dir: bool = False,
) -> None:
    label = str(path) if path else "experiment"
    for key, hint in _DEPRECATED_EXPERIMENT_KEYS.items():
        if key == "output_dir" and allow_output_dir:
            continue
        if key in data:
            raise ValueError(f"{label}: removed key {key!r}; {hint}")


@dataclass
class MASRunBase:
    """Shared base for all MAS run configs (lab and experiment).

    Relationship to single-agent ExperimentConfig
    ---------------------------------------------
    * ``scenarios``  ↔  variants (named execution contexts)
    * ``dataset``    ↔  dataset path (same JSON format)
    * ``evaluation`` ↔  EvaluationSpec (shared class, zero duplication)
    * ``output_dir`` ↔  output_dir (aligned with BenchmarkRunManager)
    * ``mas``        — replaces ``agent`` (multi-agent pointer instead of single manifest)
    """

    name: str
    description: str = ""

    lab_name: Optional[str] = None
    """Lab this experiment belongs to.

    Auto-discovered from a sibling ``lab-config.yaml`` file (``lab.name`` field)
    or from the ``.lab`` directory naming convention.  When set, the
    output-directory hierarchy becomes::

        <labs_root>/<lab_name>/<experiment_name>/

    instead of the flat ``benchmark_root()/<experiment_name>`` path.
    """

    mas: Optional[MASSpec] = None
    """MAS configuration pointer (required for any run)."""

    model: Optional[str] = "any"
    """Experiment/lab default LLM. Shorthand for ``models.main``.

    Agents and MAS that omit ``spec.models`` or set ``model: any`` inherit
    this. ``any`` means this experiment does not pin a provider id —
    inherit the application ``spec.models``, then local ``config.yaml``.
    """

    models: Dict[str, str] = field(default_factory=dict)
    """Slot map matching Agent/MAS ``spec.models[].id`` plus ``judge``.

    ``main`` is the turn default (same as scalar ``model``; ``models.main``
    wins if both are set). ``summarizer`` defaults the summary call when
    the agent omitted ``params.model``. ``judge`` defaults MCE; 
    ``evaluation.model`` still overrides the judge only.
    """

    @property
    def model_slots(self) -> Dict[str, str]:
        """Concrete slot map (scalar ``model`` merged into ``main``)."""
        return normalize_model_slots(model=self.model, models=self.models)

    scenarios: List[MASScenarioSpec] = field(default_factory=list)
    """Ordered list of scenario specs.

    If empty, scenarios are auto-discovered from ``mas.effective_configs_dir/*.yaml``.
    """

    dataset: Optional[Path] = None
    """Optional path to a prompts dataset JSON (same format as ExperimentConfig)."""

    dataset_filter: Dict[str, Any] = field(default_factory=dict)
    """Metadata filters applied after loading the dataset (e.g. ``group: single_agent``)."""

    dataset_limit: Optional[int] = None
    """Maximum number of dataset items to use (applied after filtering)."""

    dataset_source: Optional[Dict[str, Any]] = None
    """Optional ``spec.source`` overlay (same shape as Dataset spec.source)."""

    evaluation: Optional[EvaluationSpec] = None
    """Evaluation spec — reused verbatim from ExperimentConfig conventions."""

    output_dir: Path = field(default_factory=lambda: Path("./output"))
    """Where per-run outputs are written (JSONL feeds, artefacts, metrics)."""

    trace_cache_dir: Optional[Path] = None
    """Override the global trace-cache directory for this experiment.

    Priority chain (highest first):
    1. CLI ``--trace-cache`` flag
    2. This YAML field (``trace_cache_dir:``)
    3. Env var ``MAS_TRACE_CACHE``
    4. Default ``$XDG_CACHE_HOME/mas/traces``
    """

    trace_cache_portable: bool = False
    """Leave the resolved LLM infra (endpoint, model mapping) out of the cache key.

    Set it on experiments whose trace cache is published: a reader with another
    LLM endpoint then hits the same entries, as if the runs had been recorded
    on their machine. The agents' own model names still enter the key.
    """

    pipeline: List["PipelineStepSpec"] = field(default_factory=list)
    """Inline pipeline steps declared in the experiment manifest."""

    pipeline_ref: Optional[str] = None
    """External pipeline YAML path (relative to experiment dir). Resolved at schedule time."""

    pipeline_app: Optional[Dict[str, Any]] = None
    """App bundle pipeline pointer: ``{app: name, name: pipeline.yaml}``."""

    levels: Dict[str, "LevelSpec"] = field(default_factory=dict)
    """Level sections keyed by CLI depth: ``run``, ``item``, ``scenario``, ``experiment``.

    Each level declares its own artifacts and hooks.  Experiment-level
    ``pre:`` / ``post:`` / ``artifacts:`` are stored as ``levels['experiment']``.
    Deprecated aliases ``test`` and ``application`` are rewritten at load time.
    """

    artifacts: List["ArtifactSpec"] = field(default_factory=list)
    """Experiment-level artifact declarations.

    Short form: ``metrics: metrics``.
    Long form: ``trajectory: {type: plot, path: "..."}``.
    """

    output_schema: Dict[str, Any] = field(default_factory=dict)
    """Optional paper-output contract (required files/columns).

    Validated warn-only after the experiment-level ``post:`` pipeline.
    """

    pipeline_resources: List[Dict[str, Any]] = field(default_factory=list)
    """Scoped resource declarations for the pipeline.

    Resources are instantiated at their declared scope and shared with steps
    that reference them via ``@resource:<name>`` in their config.

    Example::

        pipeline_resources:
          - name: shared-metrics
            type: metrics
            scope: item      # persists across runs within a dataset item
    """

    # Keep a reference to the source file for relative-path resolution.
    _path: Optional[Path] = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------
    # Shared accessors
    # ------------------------------------------------------------------

    def all_artifacts(self) -> Dict[str, "ArtifactSpec"]:
        """Return all artifacts across all levels + experiment, keyed by name."""
        result: Dict[str, ArtifactSpec] = {}
        for level_spec in self.levels.values():
            for art in level_spec.artifacts:
                result[art.name] = art
        for art in self.artifacts:
            result[art.name] = art
        return result

    def declared_artifacts(self) -> List[tuple[str, "ArtifactSpec"]]:
        """Return ``(level, spec)`` for every artifact declared on this experiment.

        Level is ``experiment`` for the top-level ``artifacts:`` map, otherwise
        the section name (``run``, ``item``, ``scenario``).
        """
        rows: List[tuple[str, ArtifactSpec]] = [
            ("experiment", art) for art in self.artifacts
        ]
        for level_name in ("run", "item", "scenario"):
            if level_name in self.levels:
                rows.extend(
                    (level_name, art) for art in self.levels[level_name].artifacts
                )
        return rows

    def all_pipeline_steps(self) -> List["PipelineStepSpec"]:
        """Return all pipeline steps from all levels + experiment.

        Steps from inner levels come first (run, item, scenario) then
        experiment-level.  Each step has its ``scope`` set.

        Both level ``pre``/``post`` hooks and the flat ``pipeline`` field on
        :class:`MASRunBase` are combined (level hooks first).
        """
        steps: List[PipelineStepSpec] = []
        for level_name in ("run", "item", "scenario", "experiment"):
            if level_name in self.levels:
                steps.extend(self.levels[level_name].pipeline)
        steps.extend(self.pipeline)
        return steps

    def scenario_ids(self) -> List[str]:
        """Return the ordered list of scenario IDs.

        Uses the declared ``scenarios`` list when present (preserves order and
        allows custom descriptions).  Falls back to alphabetical discovery
        from ``mas.effective_configs_dir`` when the list is empty.
        """
        if self.scenarios:
            return [s.id for s in self.scenarios]
        if self.mas:
            cd = self.mas.effective_configs_dir
            if cd and cd.exists():
                return discover_scenario_stems(cd)
        return []

    def get_scenario(self, scenario_id: str) -> Optional[MASScenarioSpec]:
        """Look up a declared scenario by ID (returns None if not found)."""
        for s in self.scenarios:
            if s.id == scenario_id:
                return s
        return None

    def configs_dir(self) -> Optional[Path]:
        """Return the MAS configs directory (convenience accessor)."""
        return self.mas.effective_configs_dir if self.mas else None

    # ------------------------------------------------------------------
    # Internal YAML loader helper
    # ------------------------------------------------------------------

    @classmethod
    def _load_base_fields(
        cls,
        data: Dict[str, Any],
        base_dir: Path,
        yaml_path: Optional[Path] = None,
        allow_output_dir: bool = False,
    ) -> Dict[str, Any]:
        """Parse the shared fields from a raw YAML dict.

        *yaml_path* is the source file path — used for lab-context discovery
        (``lab-config.yaml`` neighbour or ``.lab`` parent-directory convention).
        Pass it whenever the path is known.

        Returns a kwargs dict that subclass ``from_yaml`` methods can spread
        into their constructors.
        """
        from mas.lab import paths as _paths

        _reject_deprecated_experiment_keys(
            data, path=yaml_path, allow_output_dir=allow_output_dir
        )
        canonicalize_experiment_dict(data, path=yaml_path)

        mas: Optional[MASSpec] = None
        mas_binding = data.get("application")
        if _is_mas_binding(mas_binding):
            mas = MASSpec.from_dict(mas_binding, base_dir)
        elif "applications" in data:
            # applications: [{app: example-library:example-app@v2}] or "example-library:example-app@v2"
            apps_list = data["applications"]
            if isinstance(apps_list, list) and apps_list:
                mas = MASSpec.from_dict(apps_list[0], base_dir)
        elif data.get("mas"):
            # Pre-applications alias: mas: {manifest|app, configs_dir}
            from mas.lab.deprecations import warn_deprecated

            code = (
                "lab.mas"
                if yaml_path is not None and yaml_path.name == "lab-config.yaml"
                else "experiment.mas"
            )
            warn_deprecated(code, where=str(yaml_path or "experiment"))
            mas_raw = data["mas"]
            if isinstance(mas_raw, list) and mas_raw:
                mas = MASSpec.from_dict(mas_raw[0], base_dir)
            else:
                mas = MASSpec.from_dict(mas_raw, base_dir)

        scenarios = [
            MASScenarioSpec.from_dict(s, base_dir)
            for s in data.get("scenarios", [])
        ]

        dataset: Optional[Path] = None
        dataset_filter: Dict[str, Any] = {}
        dataset_limit: Optional[int] = None
        dataset_source: Optional[Dict[str, Any]] = None
        if "dataset" in data:
            ds = data["dataset"]
            if "app" in ds:
                # Legacy: {app_root}/datasets/<name>.yaml. App-specific datasets
                # live at apps/<app>/vN/datasets/<name>/. Prefer dataset.name as a
                # catalog id; this field is only a filesystem hint.
                from mas.apps import get_app
                from mas.library_catalog import _dataset_file_in_dir, resolve_catalog_id

                _dataset_name = ds.get("name", "benchmark")
                app_root = get_app(ds["app"], base_dir)
                legacy = (app_root / "datasets" / f"{_dataset_name}.yaml").resolve()
                if legacy.is_file():
                    dataset = legacy
                else:
                    versioned_dir = app_root / "datasets" / str(_dataset_name)
                    found = _dataset_file_in_dir(versioned_dir)
                    dataset = found or resolve_catalog_id(str(_dataset_name), kind="dataset")
                    if dataset is None:
                        dataset = legacy
            elif "name" in ds:
                locator = ds.get("locator")
                if locator:
                    from mas.lab.benchmark.experiment import _resolve_dataset_by_name

                    dataset = _resolve_dataset_by_name(
                        base_dir, str(ds["name"]), locator=str(locator)
                    )
                else:
                    from mas.lab.benchmark.experiment import _resolve_dataset_by_name

                    try:
                        dataset = _resolve_dataset_by_name(
                            base_dir, str(ds["name"]), locator=None
                        )
                    except FileNotFoundError:
                        dataset = resolve_path_ref(
                            f"datasets/{ds['name']}.yaml", base_dir
                        )
            else:
                dataset = resolve_path_ref(ds["path"], base_dir)
            # Filtering: group shorthand or explicit filter dict
            if "group" in ds:
                dataset_filter["group"] = ds["group"]
            if "filter" in ds:
                dataset_filter.update(ds["filter"])
            if "limit" in ds:
                dataset_limit = int(ds["limit"])
            if isinstance(ds.get("source"), dict):
                dataset_source = dict(ds["source"])
            if (
                dataset is not None
                and mas is not None
                and mas.manifest is not None
                and Path(dataset).is_file()
            ):
                from mas.library_catalog import ensure_dataset_supports_app

                ensure_dataset_supports_app(Path(dataset), mas.manifest.parent)

        evaluation: Optional[EvaluationSpec] = None
        if "evaluation" in data:
            evaluation = EvaluationSpec.from_dict(data["evaluation"])

        # Explicit output paths remain supported by interactive lab configs.
        exp_name = data.get("name", "unnamed")
        lab_name: Optional[str] = None
        if yaml_path is not None:
            lab_name = _discover_lab_name(yaml_path)
        if allow_output_dir and "output_dir" in data:
            explicit_output_dir = Path(data["output_dir"]).expanduser()
            output_dir = (
                explicit_output_dir
                if explicit_output_dir.is_absolute()
                else base_dir / explicit_output_dir
            ).resolve()
        elif lab_name:
            output_dir = _paths.labs_root() / lab_name / exp_name
        else:
            output_dir = _paths.benchmark_root() / exp_name

        trace_cache_dir: Optional[Path] = None
        if "trace_cache_dir" in data:
            trace_cache_dir = (base_dir / data["trace_cache_dir"]).resolve()
        trace_cache_portable = bool(data.get("trace_cache_portable", False))

        pipeline: List["PipelineStepSpec"] = []
        pipeline_ref: Optional[str] = None
        pipeline_app: Optional[Dict[str, Any]] = None

        pipeline_resources: List[Dict[str, Any]] = data.get("pipeline_resources", [])

        levels: Dict[str, LevelSpec] = {}
        for level_name in ("run", "item", "scenario"):
            if level_name in data:
                canonical = "item" if level_name == "test" else level_name
                levels[canonical] = LevelSpec.from_dict(
                    canonical, data[level_name], base_dir=base_dir
                )

        # Experiment-level hooks live on the experiment object (CLI --depth exp).
        if data.get("pre") or data.get("post"):
            levels["experiment"] = LevelSpec.from_dict(
                "experiment",
                {
                    "pre": data.get("pre") or [],
                    "post": data.get("post") or [],
                    "artifacts": data.get("artifacts") or {},
                },
                base_dir=base_dir,
            )

        # Experiment-level artifacts (when not already consumed as level artifacts)
        artifacts: List[ArtifactSpec] = [
            ArtifactSpec.from_entry(name, value)
            for name, value in data.get("artifacts", {}).items()
        ]

        if "experiment" in levels:
            for step in levels["experiment"].pipeline:
                if not step.scope:
                    step.scope = "experiment"

        raw_models = data.get("models") if isinstance(data.get("models"), dict) else {}
        authored_models = {
            str(k).strip(): ("" if v is None else str(v).strip())
            for k, v in raw_models.items()
            if str(k).strip()
        }
        slots = normalize_model_slots(model=data.get("model"), models=raw_models)
        model = slots.get("main") or ANY_MODEL

        _inject_eval_mce_judge_model(
            levels,
            evaluation,
            data.get("metadata"),
            mas=mas,
            experiment_model=slots.get("main"),
            experiment_judge_model=slots.get("judge"),
        )

        return dict(
            name=exp_name,
            description=data.get("description", ""),
            lab_name=lab_name,
            mas=mas,
            model=model,
            models=authored_models,
            scenarios=scenarios,
            dataset=dataset,
            dataset_filter=dataset_filter,
            dataset_limit=dataset_limit,
            dataset_source=dataset_source,
            evaluation=evaluation,
            output_dir=output_dir,
            trace_cache_dir=trace_cache_dir,
            trace_cache_portable=trace_cache_portable,
            pipeline=pipeline,
            pipeline_ref=pipeline_ref,
            pipeline_app=pipeline_app,
            pipeline_resources=pipeline_resources,
            levels=levels,
            artifacts=artifacts,
            output_schema=dict(data.get("output_schema") or {}),
        )


def _inject_eval_mce_judge_model(
    levels: Dict[str, "LevelSpec"],
    evaluation: Optional[EvaluationSpec],
    metadata: Any,
    mas: Optional[MASSpec] = None,
    experiment_model: Any = None,
    experiment_judge_model: Any = None,
) -> None:
    """Fill omitted eval_mce config.model from evaluation / experiment / application."""
    try:
        from mas.library.eval.mce.judge_model import (
            apply_eval_mce_model_defaults,
            resolve_judge_model,
            unique_application_model,
        )
    except ImportError:
        return
    app_model, app_source = unique_application_model(
        mas.manifest if mas is not None else None
    )
    resolved = resolve_judge_model(
        evaluation_model=evaluation.model if evaluation else None,
        evaluation_config=evaluation.config if evaluation else None,
        metadata=metadata if isinstance(metadata, dict) else None,
        application_model=app_model,
        application_source=app_source or "application.spec.models",
        experiment_model=experiment_model,
        experiment_judge_model=experiment_judge_model,
    )
    if not resolved.model:
        return
    for level in levels.values():
        apply_eval_mce_model_defaults(list(level.pipeline), resolved)

