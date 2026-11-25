#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""
Experiment configuration for agent evaluations.

An experiment defines:
- Dataset to evaluate on
- Agent specification (with scenarios/patterns)
- Execution flavours (local, distributed, etc.)
- Evaluation method (user emulation, metrics)
- Plots to generate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from mas.lab.lab.config import OverlayEntry

logger = logging.getLogger(__name__)


@dataclass
class AgentSpec:
    """Agent specification with scenarios."""

    base_manifest: Path
    """Base agent manifest (YAML)."""

    scenarios: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """Scenarios to apply (e.g., design patterns). Each scenario is a named
    configuration that may involve one or more overlays.
    
    Example:
        {
            "cot": {"pattern": "chain_of_thought"},
            "react": {"pattern": "react"},
        }
    """

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: Path) -> AgentSpec:
        from mas.runtime.spec.source import resolve_yaml_path

        manifest = resolve_yaml_path(str(data["manifest"]), base_dir)
        scenarios = data.get("scenarios", {})
        resolved_scenarios = {}
        for scen_name, scen_config in scenarios.items():
            resolved_config = scen_config.copy()
            if "manifest" in resolved_config:
                resolved_config["manifest_path"] = str(resolve_yaml_path(str(resolved_config["manifest"]), base_dir))
            resolved_scenarios[scen_name] = resolved_config

        return cls(base_manifest=manifest, scenarios=resolved_scenarios)


@dataclass
class FlavourSpec:
    """Execution flavour (runtime configuration)."""

    name: str
    """Flavour name (e.g., "local", "distributed")."""

    config: Dict[str, Any] = field(default_factory=dict)
    """Flavour-specific configuration.
    
    Example:
        {
            "mode": "single_process",
            "instrumentation": "otel",
            "timeout": 60
        }
    """

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> FlavourSpec:
        return cls(name=name, config=data)


@dataclass
class EvaluationSpec:
    """Evaluation method specification."""

    method: str
    """Evaluation method: "user_emulation", "llm_judge", "metrics"."""

    config: Dict[str, Any] = field(default_factory=dict)
    """Method-specific configuration.
    
    For user_emulation:
        {
            "emulator_manifest": "path/to/emulator.yaml",
            "criteria": ["correctness", "helpfulness"],
        }

    ``config.model`` is an alias of ``evaluation.model`` (the top-level field
    wins when both are set).
    """

    model: Optional[str] = None
    """LLM-as-judge override for eval_mce.

    Default: experiment.models.judge, then experiment.model / models.main,
    then application spec.models[].
    """

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvaluationSpec:
        config = dict(data.get("config") or {})
        model = data.get("model") or config.get("model") or config.get("judge_model")
        return cls(
            method=data["method"],
            model=str(model).strip() if model else None,
            config=config,
        )


@dataclass
class PlotSpec:
    """Plot specification."""

    name: str
    type: str
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> PlotSpec:
        return cls(name=name, type=data["type"], params=data.get("params", {}))


def _scan_dir_for_dataset_name(directory: Path, name: str) -> Optional[Path]:
    """Scan *directory* for a Dataset manifest whose metadata.name matches *name*.

    Returns the matching Path or None.  Non-manifest YAML files (no kind:Dataset)
    are matched by file stem as a last resort so unlabelled legacy files still work.
    """
    if not directory.is_dir():
        return None
    stem_fallback: Optional[Path] = None
    for candidate in sorted(directory.glob("*.yaml")):
        try:
            from mas.runtime.spec.source import load_yaml_file

            data = load_yaml_file(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        meta_name = (data.get("metadata") or {}).get("name") or data.get("name")
        if meta_name == name:
            return candidate
        # Track stem match as a fallback for files without metadata.name
        if candidate.stem == name and stem_fallback is None:
            stem_fallback = candidate
    return stem_fallback


def _resolve_dataset_by_name(base_dir: Path, name: str, locator: Optional[str] = None) -> Path:
    """Resolve a Dataset manifest by its metadata.name.

    **locator values:**

    ``None`` / ``"local"``
        Search ``base_dir/datasets``, then ``base_dir``, then ancestor
        ``datasets/`` folders (including past a nested ``lab-config.yaml``
        so ``01-foo/experiment.yaml`` still sees the lab root pack).

    ``"<package-name>"``
        Search inside an installed Python package or a workspace library path.
        Tries (in order):

        1. ``importlib.resources.files(package).joinpath("datasets")`` — works
           for any package installed in the active venv.
        2. sys.path scan — catches paths injected via the workspace
           ``libraries:`` key in ``config.yaml``.  Each entry is
           treated as a root; the function looks for ``<root>/datasets/``.

        Use this form when a dataset is shared across many labs and lives in
        a manifest library root (e.g. ``locator: library-samples``).

    Cross-lab references are deliberately **not** supported.  If a dataset is
    needed by multiple labs, put it in a library package and install it (or
    add the local path to ``libraries:`` in ``config.yaml``).
    """
    if not locator or locator == "local":
        # ── Local lookup ──────────────────────────────────────────────────
        for search_dir in [base_dir / "datasets", base_dir]:
            result = _scan_dir_for_dataset_name(search_dir, name)
            if result:
                return result
        # Bare filename fallback (no YAML loading needed)
        for search_dir in [base_dir / "datasets", base_dir]:
            candidate = search_dir / f"{name}.yaml"
            if candidate.exists():
                return candidate
        for parent in [base_dir, *base_dir.parents]:
            lab_datasets = parent / "datasets"
            if not lab_datasets.is_dir() and not (parent / "lab-config.yaml").is_file():
                continue
            result = _scan_dir_for_dataset_name(lab_datasets, name)
            if result:
                return result
            candidate = lab_datasets / f"{name}.yaml"
            if candidate.exists():
                return candidate
        # ── Library fallback: scan sys.path entries injected by inject_lab_libraries
        # Each entry is a library root; look for <root>/datasets/
        import sys as _sys

        for sys_entry in _sys.path:
            datasets_dir = Path(sys_entry) / "datasets"
            result = _scan_dir_for_dataset_name(datasets_dir, name)
            if result:
                return result
        raise FileNotFoundError(
            f"No Dataset named {name!r} found locally in {base_dir} or in "
            f"any lab library (sys.path entries).  "
            f"Add dataset.locator or install the library package that contains this dataset."
        )

    # ── Package / library locator ─────────────────────────────────────────
    return _resolve_dataset_from_package(name, locator)


def _resolve_dataset_from_package(name: str, package: str) -> Path:
    """Resolve dataset *name* from a library locator (scheme or package name).

    Tries manifest-library scheme roots first (``samples``, ``standard``),
    then importlib.resources (installed packages), then sys.path scan.
    """
    import sys

    from mas.runtime.package_refs import resolve_library_scheme_root

    scheme_root = resolve_library_scheme_root(package)
    if scheme_root is not None:
        from mas.library_catalog import _index_datasets, _lookup_versioned

        hit = _lookup_versioned(_index_datasets(scheme_root), name)
        if hit is not None:
            return hit
        result = _scan_dir_for_dataset_name(scheme_root / "datasets", name)
        if result:
            return result

    # 1. Installed package via importlib.resources
    try:
        import contextlib
        import importlib.resources

        _res = importlib.resources.files(package).joinpath("datasets")
        with contextlib.ExitStack() as _stk:
            pkg_dir = _stk.enter_context(importlib.resources.as_file(_res))
            result = _scan_dir_for_dataset_name(Path(pkg_dir), name)
            if result:
                return result
    except (ModuleNotFoundError, FileNotFoundError, TypeError, Exception):
        logger.debug("suppressed", exc_info=True)

    # 2. sys.path scan — for local library paths injected by mas-lab
    # Package "my.sub" maps to "my/sub"; bare name "my-lib" stays as-is
    pkg_subpath = package.replace(".", "/")
    for sys_entry in sys.path:
        for subpath in [package] if package == pkg_subpath else [package, pkg_subpath]:
            datasets_dir = Path(sys_entry) / subpath / "datasets"
            result = _scan_dir_for_dataset_name(datasets_dir, name)
            if result:
                return result

    raise FileNotFoundError(
        f"No Dataset named {name!r} in package {package!r}. "
        f"Is the package installed or listed under 'libraries:' in config.yaml?"
    )


@dataclass
class ExperimentConfig:
    """Complete experiment configuration."""

    name: str
    description: str

    dataset: Path
    """Resolved path to the dataset file (after name-or-path resolution)."""

    flavours: List[FlavourSpec]
    """Execution flavours to test."""

    execution: Dict[str, Any]
    """Leftover mapping from a nested ``execution:`` block (prefer ``n_runs``)."""

    n_runs: int = 1
    """Replication count from ``run.n_runs`` (or leftover ``execution.n_runs``)."""

    dataset_source: Optional[Dict[str, Any]] = None
    """Optional ``spec.source`` overlay merged onto the Dataset at load time."""

    agent: Optional[AgentSpec] = None
    """Agent specification with scenarios (single-scenario mode)."""

    scenarios: Dict[str, AgentSpec] = field(default_factory=dict)
    """Multiple scenarios (multi-scenario mode). Keys are scenario names."""

    evaluation: Optional[EvaluationSpec] = None
    """Evaluation method (optional)."""

    plots: List[PlotSpec] = field(default_factory=list)
    """Plots to generate."""

    output_dir: Path = Path("./output")
    """Output directory."""

    overlays_dir: Optional[Path] = None
    """Directory containing overlay files (for registry-based lookup)."""

    overlay_registry: Dict[str, "OverlayEntry"] = field(default_factory=dict)
    """Overlay registry: id → OverlayEntry. Built from overlays_dir."""

    base_dir: Path = field(default_factory=lambda: Path("."))
    """Base directory of the experiment (where experiment.yaml lives)."""

    @property
    def is_multi_scenario(self) -> bool:
        """Check if experiment uses multiple scenarios."""
        return len(self.scenarios) > 0

    @classmethod
    def from_yaml(cls, path: Path) -> ExperimentConfig:
        """Load experiment config from YAML.

        Expected structure:
            experiment:
              name: "design-patterns-eval"
              description: "..."

              dataset:
                path: "./datasets/math_reasoning.yaml"
                filter:  # optional
                  category: "arithmetic"

              agent:
                manifest: "./agents/base_agent.yaml"
                scenarios:
                  cot:
                    pattern: "chain_of_thought"
                  react:
                    pattern: "react"

              flavours:
                local:
                  mode: "single_process"
                  timeout: 60
                distributed:
                  mode: "multi_process"
                  timeout: 120

              run:
                n_runs: 5
              schedule:
                plugin: coverage
                parallel_scenarios: 4  # max concurrent scenarios
                pause_between_runs: 1.0

              evaluation:
                method: "user_emulation"
                config:
                  emulator_manifest: "./emulator.yaml"
                  criteria: ["correctness", "helpfulness"]

              plots:
                latency_by_scenario:
                  type: "latency_distribution"
                  params:
                    facet_by: "scenario"
                success_rate:
                  type: "success_rate"
        """
        from mas.lab.manifests.loader import load_experiment_data

        data, _version = load_experiment_data(path)
        exp_data = data.get("experiment", data)
        base_dir = path.parent

        # Discover and inject lab context (libraries for plugin imports)
        from mas.lab.lab.config import discover_lab_context, inject_lab_libraries

        lab_context = discover_lab_context(path)
        inject_lab_libraries(lab_context)

        # Explicit paths take precedence over registry-based name resolution.
        dataset_config = exp_data["dataset"]
        if "path" in dataset_config:
            from mas.runtime.spec.source import resolve_yaml_path

            dataset_path = resolve_yaml_path(str(dataset_config["path"]), base_dir)
        elif "name" in dataset_config:
            dataset_path = _resolve_dataset_by_name(
                base_dir,
                dataset_config["name"],
                locator=dataset_config.get("locator"),
            )
        else:
            raise ValueError(
                "dataset must have a 'path' or 'name' key. Use 'path' for an explicit "
                "dataset YAML, or 'name' for registry-based resolution."
            )

        # Parse agent(s) - support both single and multi-scenario formats
        agent = None
        scenarios = {}

        if "scenarios" in exp_data:
            scenarios_data = exp_data["scenarios"]
            if isinstance(scenarios_data, list):
                # Resolve app manifest: mas.manifest > application.manifest
                # > application.app > applications[0] > agent.yaml
                mas_config = exp_data.get("mas", {})
                app = exp_data.get("application")
                apps = exp_data.get("applications", [])
                binding = app if isinstance(app, dict) else {}
                if not binding.get("app") and not binding.get("manifest"):
                    binding = apps[0] if apps else {}
                app_name = binding.get("app")

                if mas_config.get("manifest"):
                    # Explicit manifest path in mas.manifest
                    app_manifest = base_dir / mas_config["manifest"]
                elif binding.get("manifest"):
                    app_manifest = (base_dir / binding["manifest"]).resolve()
                elif app_name:
                    try:
                        from mas.apps import get_app, resolve_app_manifest

                        app_manifest = resolve_app_manifest(get_app(app_name), app_id=str(app_name))
                    except Exception:
                        app_manifest = base_dir / "agent.yaml"
                else:
                    app_manifest = base_dir / "agent.yaml"
                sub_scenarios = {item["id"]: {k: v for k, v in item.items() if k != "id"} for item in scenarios_data}
                agent = AgentSpec(base_manifest=app_manifest, scenarios=sub_scenarios)
            else:
                # Old dict format: {scenario_name: {agent: ...}}
                for scenario_name, scenario_data in scenarios_data.items():
                    scenarios[scenario_name] = AgentSpec.from_dict(scenario_data["agent"], base_dir)
        elif "agent" in exp_data:
            # Single-scenario format (backward compatible)
            agent = AgentSpec.from_dict(exp_data["agent"], base_dir)
        else:
            raise ValueError("Experiment must have either 'agent' or 'scenarios' field")

        # Parse flavours
        flavours = [
            FlavourSpec.from_dict(name, config) for name, config in exp_data.get("flavours", {"local": {}}).items()
        ]

        # Parse evaluation
        evaluation = None
        if "evaluation" in exp_data:
            evaluation = EvaluationSpec.from_dict(exp_data["evaluation"])

        # Parse plots
        plots = [PlotSpec.from_dict(name, config) for name, config in exp_data.get("plots", {}).items()]

        # Output directory: explicit > labs_root/<lab>/<experiment>
        if "output_dir" in exp_data:
            explicit = Path(exp_data["output_dir"]).expanduser()
            if explicit.is_absolute():
                output_dir = explicit
            else:
                # Relative output_dir → anchor at canonical benchmark root,
                # never relative to the experiment YAML (workspace tree).
                from mas.lab import paths as _paths

                output_dir = _paths.benchmark_root() / explicit
        else:
            from mas.lab import paths as _paths

            output_dir = _paths.benchmark_root() / exp_data["name"]

        # Load overlay registry from overlays directory
        # Priority: mas.configs_dir > ./overlays
        mas_config = exp_data.get("mas", {})
        if mas_config.get("configs_dir"):
            overlays_dir = base_dir / mas_config["configs_dir"]
        else:
            overlays_dir = base_dir / "overlays"

        overlay_registry: Dict[str, "OverlayEntry"] = {}
        if overlays_dir.exists():
            try:
                from mas.lab.lab.config import load_overlay_registry

                overlay_registry = load_overlay_registry(overlays_dir)
                if overlay_registry:
                    logger.debug(
                        "Loaded %d overlays from %s",
                        len(overlay_registry),
                        overlays_dir,
                    )
            except Exception as e:
                logger.warning("Failed to load overlay registry from %s: %s", overlays_dir, e)

        dataset_source = None
        if isinstance(dataset_config.get("source"), dict):
            dataset_source = dict(dataset_config["source"])

        run_block = exp_data.get("run") if isinstance(exp_data.get("run"), dict) else {}
        execution = dict(exp_data.get("execution") or {})
        if "n_runs" in run_block:
            n_runs = max(1, int(run_block["n_runs"]))
        elif "n_runs" in execution:
            n_runs = max(1, int(execution["n_runs"]))
        else:
            n_runs = 1
        execution["n_runs"] = n_runs

        return cls(
            name=exp_data["name"],
            description=exp_data.get("description", ""),
            dataset=dataset_path,
            dataset_source=dataset_source,
            agent=agent,
            scenarios=scenarios,
            flavours=flavours,
            execution=execution,
            n_runs=n_runs,
            evaluation=evaluation,
            plots=plots,
            output_dir=output_dir,
            overlays_dir=overlays_dir if overlays_dir.exists() else None,
            overlay_registry=overlay_registry,
            base_dir=base_dir,
        )

    def generate_scenarios(
        self,
        selected_scenarios: Optional[List[str]] = None,
        max_runs: Optional[int] = None,
        limit_scenarios: Optional[int] = None,
        sample_scenarios: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Generate all scenarios to execute.

        A scenario slot is: (scenario_name × scenario × flavour × dataset_item × run_id)
        Tests are deduplicated automatically - same test executed only once.

        Args:
            selected_scenarios: Optional list of scenario names to execute (None = all)
            max_runs: Override n_runs from config (useful for testing)
            limit_scenarios: Limit to first N scenarios (applied after sampling)
            sample_scenarios: Randomly sample N scenarios before limiting

        Returns:
            List of scenario specifications
        """
        import random

        from mas.lab.benchmark import Dataset

        # Load dataset
        dataset = Dataset.from_yaml(
            self.dataset,
            source_overlay=self.dataset_source,
        )
        n_runs = max_runs if max_runs is not None else self.n_runs

        scenarios = []

        # Determine which agent specs to use
        if self.is_multi_scenario:
            # Multi-scenario mode
            agent_specs = self.scenarios

            # Filter if specific scenarios requested
            if selected_scenarios:
                agent_specs = {name: spec for name, spec in agent_specs.items() if name in selected_scenarios}
        else:
            # Single-scenario mode (backward compatible)
            agent_specs = {"default": self.agent}

        # Generate all scenario slots
        for scenario_name, agent_spec in agent_specs.items():
            for scen_name in agent_spec.scenarios.keys():
                for flavour in self.flavours:
                    for item in dataset.items:
                        for run_id in range(1, n_runs + 1):
                            scenario_config = agent_spec.scenarios[scen_name]

                            overlay_refs = scenario_config.get("overlays", [])
                            if isinstance(overlay_refs, str):
                                overlay_refs = [overlay_refs]

                            overlay_paths: List[str] = []
                            if overlay_refs:
                                try:
                                    from mas.ctl.paths import resolve_overlay_ref_entries

                                    manifest_dir = self.base_dir
                                    overlay_paths = [
                                        str(p)
                                        for p in resolve_overlay_ref_entries(
                                            overlay_refs,
                                            manifest_dir=manifest_dir,
                                            overlays_dir=self.overlays_dir,
                                            base_dir=self.base_dir,
                                        )
                                    ]
                                except Exception as e:
                                    logger.warning(
                                        "Failed to resolve overlays %s for scenario %s: %s",
                                        overlay_refs,
                                        scen_name,
                                        e,
                                    )

                            # Backward compatibility: manifest_path takes precedence
                            legacy_manifest = scenario_config.get("manifest_path")

                            scenarios.append(
                                {
                                    "scenario_name": scenario_name,
                                    "scenario": scen_name,
                                    "flavour": flavour.name,
                                    "dataset_item": item,
                                    "run_id": run_id,
                                    "scenario_config": scenario_config,
                                    "flavour_config": flavour.config,
                                    "base_manifest": str(agent_spec.base_manifest),
                                    "overlay_refs": list(overlay_refs),
                                    "overlay_paths": overlay_paths,
                                    # Legacy: single overlay manifest (deprecated)
                                    "overlay_manifest": legacy_manifest,
                                }
                            )

        # Apply sampling if requested
        if sample_scenarios is not None and sample_scenarios < len(scenarios):
            random.shuffle(scenarios)
            scenarios = scenarios[:sample_scenarios]

        # Apply limit if requested
        if limit_scenarios is not None and limit_scenarios < len(scenarios):
            scenarios = scenarios[:limit_scenarios]

        return scenarios
