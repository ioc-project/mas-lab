#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

"""
Pipeline step base classes and YAML pipeline loader.
"""


import asyncio
import importlib
import importlib.util
import inspect
import sys
import warnings as _warnings_module
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

import yaml
from mas.lab.benchmark.pipeline.models import (
    _STEP_KNOWN_KEYS,
    ConfigParam,
    PipelineConfig,
    StepManifest,
    StepOutput,
)
from mas.runtime.registry import get_registry, register_plugin

# ---------------------------------------------------------------------------
# Step type resolution
#
# There is no bench-local step registry. Step *implementations* are library
# plugins (see library-lab/library.yaml's types:/plugins: block) registered into
# the single mas.runtime.registry singleton through the generic manifest/
# fixpoint mechanism (runtime/docs/plugin-registry-manifests.md) -- the
# functions below are a thin wrapper over get_registry(), plus one genuinely
# engine-specific feature: resolving a step type string that ISN'T a
# registered plugin at all, but a raw 'module.path:ClassName' or
# './file.py:ClassName' reference straight from pipeline YAML.
# ---------------------------------------------------------------------------


def register_step(name: str, obj: Any, *, attributes: dict[str, Any] | None = None) -> None:
    """Register a pipeline step class in the runtime registry."""
    if not isinstance(obj, type):
        raise TypeError(f"Pipeline step {name!r} must be a class, got {type(obj)!r}")
    attrs = dict(attributes or {})
    source = str(attrs.pop("source", None) or "programmatic")
    register_plugin(
        f"mas.step.{str(name).strip().lower().replace('-', '_').replace('.', '_')}",
        obj,
        shortcuts=[str(name)],
        attributes={"source": source, **attrs},
    )


def register_step_type(step_type: str, step_class: type) -> None:
    """Deprecated alias for :func:`register_step`, kept for backward compatibility.

    Lab-local step modules (``labs/*.lab/<lab>_lab/steps/*.py``) still import
    and call this name.
    """
    register_step(step_type, step_class)


def get_step(name: str | None = None, *, attributes: dict[str, Any] | None = None) -> Any:
    """Get one pipeline step by optional name and optional attribute match."""
    registry = get_registry()
    info = registry.get("step", str(name) if name else None, attributes=attributes)
    return info.load_class() if info is not None else None


def list_steps() -> dict[str, Any]:
    """Return registered pipeline steps as ``{name: class_or_none}``.

    Listing does not import step modules or their dependencies. A class is
    returned only when that module is already in ``sys.modules`` (typically
    a programmatic :func:`register_step`). Use :func:`get_step` to load one.
    """
    items: dict[str, Any] = {}
    for entry in get_registry().list():
        if str(entry.get("category") or "") != "step":
            continue
        shortcuts = entry.get("shortcuts") or []
        step_name = str(shortcuts[0]) if shortcuts else str(entry.get("urn") or "").rsplit(".", 1)[-1]
        if not step_name:
            continue
        module = str(entry.get("module") or "")
        class_name = str(entry.get("class_name") or "")
        cls = None
        if module and class_name:
            loaded = sys.modules.get(module)
            if loaded is not None:
                cls = getattr(loaded, class_name, None)
        items[step_name] = cls
    return dict(sorted(items.items()))


def _import_class(spec: str, base_dir: Optional[Path] = None) -> type:
    """Fallback for step types that aren't registered plugins at all: a raw
    ``module.path:ClassName`` or ``./file.py:ClassName`` reference straight
    from pipeline YAML."""
    if ".py:" in spec:
        file_part, class_name = spec.rsplit(":", 1)
        file_path = Path(file_part)
        if not file_path.is_absolute():
            root = base_dir or Path.cwd()
            file_path = root / file_path
        file_path = file_path.resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Step file not found: {file_path}")
        module_name = f"_user_steps.{file_path.stem}"
        spec_obj = importlib.util.spec_from_file_location(module_name, file_path)
        if spec_obj is None or spec_obj.loader is None:
            raise ImportError(f"Cannot load module from {file_path}")
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[module_name] = module
        spec_obj.loader.exec_module(module)
        return getattr(module, class_name)

    if ":" in spec:
        module_path, class_name = spec.rsplit(":", 1)
    elif "." in spec:
        module_path, class_name = spec.rsplit(".", 1)
    else:
        raise ValueError(
            f"Cannot resolve step type {spec!r}. "
            f"Use a registered name, 'module.path:ClassName', or './file.py:ClassName'."
        )

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def resolve_step_class(name: str, *, base_dir: Optional[Path] = None, required_base: type | None = None) -> type:
    """Resolve a pipeline step class by name, with module/class fallback."""
    if name == "__concurrent_runner__":
        raise ValueError(
            "step type '__concurrent_runner__' was removed; the scheduler runs "
            "each instance as its own node (use concurrency: N as a cap)"
        )

    try:
        cls = get_step(name)
    except Exception as exc:
        cls = None
        _manifest_lookup_error: Exception | None = exc
    else:
        _manifest_lookup_error = None

    if cls is None:
        try:
            cls = _import_class(name, base_dir=base_dir)
        except Exception as exc:
            known = ", ".join(sorted(list_steps().keys()))
            cause = _manifest_lookup_error or exc
            hint = ""
            if not known:
                hint = (
                    " No pipeline steps are registered. Install mas-library-lab "
                    "(workspace member) so extract_trace_stats and other library-lab "
                    "steps are on the plugin registry."
                )
            raise ValueError(
                f"Unknown step type: {name!r}. "
                f"Registered types: {known or '(none)'}.{hint} "
                f"For custom objects, use 'module.path:ClassName' or './file.py:ClassName'. "
                f"Import error: {cause}"
            ) from cause

    if required_base and not (isinstance(cls, type) and issubclass(cls, required_base)):
        raise TypeError(f"step type {name!r} resolved to {cls!r}, which is not a {required_base.__name__} subclass.")
    return cls


class PipelineStep(ABC):
    """Base class for pipeline steps."""

    type: str = "base"
    persistent: bool = False
    resource: str = "cpu"
    PARAMS: ClassVar[List[ConfigParam]] = []

    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        depends_on: Optional[List[str]] = None,
        phase: str = "post",
        per_scenario: bool = False,
        per_run: bool = False,
        scope: str = "",
        resource: str | None = None,
        refresh: str = "",
        concurrency: int = 1,
    ):
        self.name = name
        self.config = config
        self.depends_on = depends_on or []
        self.phase = phase
        self.per_scenario = per_scenario
        self.per_run = per_run
        self.scope = scope
        self.resource = str(resource or type(self).resource or "cpu")
        self.refresh = refresh
        self.concurrency = max(1, int(concurrency or 1))

    def is_persistent(self) -> bool:
        cfg_val = self.config.get("persist")
        if cfg_val is not None:
            return bool(cfg_val)
        return bool(type(self).persistent)

    def on_batch(self, batch: List[Dict[str, Any]], ctx: "ExecutionContext") -> None:
        """Fold a batch of records. Default is a no-op; override to consume streams."""
        del batch, ctx
        return None

    async def finish(self, ctx: "ExecutionContext") -> StepOutput:
        """Seal-side persist. Default delegates to :meth:`execute`."""
        return await self.execute(ctx)

    @abstractmethod
    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        pass

    def outputs_exist(self, output_dir: Path) -> bool:
        return True

    @property
    def output_artifacts(self) -> List[Tuple[str, Any]]:
        return []

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: Optional[Path] = None) -> PipelineStep:
        step_type = data["type"]
        step_class = resolve_step_class(step_type, base_dir=base_dir, required_base=PipelineStep)

        unknown_keys = {k for k in data.keys() if k not in _STEP_KNOWN_KEYS and not k.startswith("x-")}
        if unknown_keys:
            _warnings_module.warn(
                f"Step {data.get('name', '?')!r}: unknown key(s) {sorted(unknown_keys)!r} "
                f"in pipeline YAML.  Did you mean to put them under 'config:'?",
                stacklevel=2,
            )

        cfg = dict(data.get("config", {}))
        if "processor" in data and "processor" not in cfg:
            cfg.setdefault("processor", data["processor"])

        per_scenario = bool(data.get("per_scenario", False))
        per_run = bool(data.get("per_run", False))
        scope = str(data.get("scope") or "")
        resource = str(data.get("resource") or step_class.__dict__.get("resource") or "cpu")
        refresh = str(data.get("refresh") or "")
        try:
            concurrency = int(data.get("concurrency") or 1)
        except (TypeError, ValueError):
            concurrency = 1
        try:
            step = step_class(
                name=data["name"],
                config=cfg,
                depends_on=data.get("depends_on", []),
                phase=data.get("phase", "post"),
            )
        except TypeError:
            step = step_class(
                name=data["name"],
                config=cfg,
                depends_on=data.get("depends_on", []),
            )
        step.phase = str(data.get("phase") or "post")
        step.per_scenario = per_scenario
        step.per_run = per_run
        step.scope = scope
        step.resource = resource
        step.refresh = refresh
        step.concurrency = max(1, concurrency)
        return step

    @classmethod
    def manifest(cls) -> Optional[StepManifest]:
        src_file = inspect.getfile(cls)
        manifest_file = Path(src_file).with_suffix(".yaml")
        if manifest_file.exists():
            return StepManifest.from_yaml(manifest_file)
        return None


class BatchPipelineStep(PipelineStep, ABC):
    """Base class for steps that process a collection of items independently."""

    @abstractmethod
    async def process_one(
        self,
        item: Any,
        ctx: "ExecutionContext",
    ) -> StepOutput:
        pass

    def _get_items(self, ctx: "ExecutionContext") -> List[Any]:
        items = self.config.get("items", [])
        if not items:
            raise ValueError(
                f"Step '{self.name}': no 'items' in config.  Override _get_items() or provide config.items."
            )
        return items

    def _merge_results(
        self,
        items: List[Any],
        results: List[StepOutput],
        ctx: "ExecutionContext",
    ) -> StepOutput:
        all_files: List[Path] = []
        all_data: List[Dict[str, Any]] = []
        all_meta: List[Dict[str, Any]] = []
        for so in results:
            all_files.extend(so.files)
            all_data.append(so.data)
            all_meta.append(so.metadata)
        return StepOutput(
            data={"results": all_data, "items": items},
            files=all_files,
            metadata={"item_count": len(items), "results": all_meta},
        )

    async def execute(self, ctx: "ExecutionContext") -> StepOutput:
        items = self._get_items(ctx)
        concurrency = int(self.config.get("concurrency", 1))

        if concurrency > 1:
            sem = asyncio.Semaphore(concurrency)

            async def _bounded(item: Any) -> StepOutput:
                async with sem:
                    return await self.process_one(item, ctx)

            results = await asyncio.gather(*[_bounded(item) for item in items])
        else:
            results = []
            for item in items:
                results.append(await self.process_one(item, ctx))

        return self._merge_results(items, list(results), ctx)


class YAMLIncludeLoader(yaml.SafeLoader):
    """Custom YAML loader with !include support."""

    def __init__(self, stream):
        self._root = Path(stream.name).parent if hasattr(stream, "name") else Path.cwd()
        super().__init__(stream)


def include_constructor(loader: YAMLIncludeLoader, node: yaml.Node) -> Any:
    path = loader.construct_scalar(node)
    include_path = loader._root / path

    with open(include_path, "r") as f:
        return yaml.load(f, YAMLIncludeLoader)


YAMLIncludeLoader.add_constructor("!include", include_constructor)


class Pipeline:
    """Declarative pipeline with dependency tracking."""

    def __init__(
        self,
        config: PipelineConfig,
        steps: List[PipelineStep],
        config_path: Optional[Path] = None,
    ):
        self.config = config
        self.steps = steps
        self.config_path = config_path
        self._step_map = {step.name: step for step in steps}
        self._validate()

    def _validate(self):
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            duplicates = [n for n in names if names.count(n) > 1]
            raise ValueError(f"Duplicate step names: {duplicates}")

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in self._step_map:
                    raise ValueError(f"Step '{step.name}' depends on unknown step '{dep}'")

        from mas.lab.benchmark.pipeline.resolver import DependencyResolver

        resolver = DependencyResolver(self)
        try:
            resolver.resolve()
        except ValueError as e:
            raise ValueError(f"Pipeline validation failed: {e}") from e

    def get_step(self, name: str) -> Optional[PipelineStep]:
        return self._step_map.get(name)

    def get_dependencies(self, step_name: str) -> List[PipelineStep]:
        step = self.get_step(step_name)
        if not step:
            return []
        return [self._step_map[dep] for dep in step.depends_on if dep in self._step_map]

    @classmethod
    def step_dicts_from_yaml(cls, path: Union[str, Path]) -> List[Dict[str, Any]]:
        """Parse step dicts from pipeline YAML without dependency validation."""
        path = Path(path)
        with open(path, "r") as f:
            raw = yaml.load(f, YAMLIncludeLoader)

        from mas.lab.manifests import normalize_manifest_version

        data, _manifest_version = normalize_manifest_version(raw, "pipeline", path)
        pipeline_data = data.get("pipeline", data)
        if "spec" in pipeline_data and "metadata" in pipeline_data:
            pipeline_data = dict(pipeline_data.get("spec", {}))

        out: List[Dict[str, Any]] = []
        for step_data in pipeline_data.get("steps", []):
            if not isinstance(step_data, dict) or "name" not in step_data or "type" not in step_data:
                continue
            out.append(dict(step_data))
        return out

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> Pipeline:
        path = Path(path)

        with open(path, "r") as f:
            raw = yaml.load(f, YAMLIncludeLoader)

        from mas.lab.manifests import normalize_manifest_version

        data, _manifest_version = normalize_manifest_version(raw, "pipeline", path)

        pipeline_data = data.get("pipeline", data)

        if "spec" in pipeline_data and "metadata" in pipeline_data:
            meta = pipeline_data.get("metadata", {})
            spec = pipeline_data.get("spec", {})
            pipeline_data = {
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                **spec,
            }

        config = PipelineConfig.from_dict(pipeline_data)
        from mas.lab.benchmark.pipeline.artifact_paths import validate_step_dicts

        step_dicts = validate_step_dicts(pipeline_data.get("steps", []))
        steps = [PipelineStep.from_dict(step_data) for step_data in step_dicts]

        return cls(config=config, steps=steps, config_path=path)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline": {
                "name": self.config.name,
                "version": self.config.version,
                "description": self.config.description,
                "output": self.config.output,
                "steps": [
                    {
                        "name": step.name,
                        "type": step.type,
                        "depends_on": step.depends_on,
                        "config": step.config,
                    }
                    for step in self.steps
                ],
            }
        }


__all__ = [
    "PipelineStep",
    "BatchPipelineStep",
    "YAMLIncludeLoader",
    "include_constructor",
    "Pipeline",
    "register_step",
    "register_step_type",
    "get_step",
    "list_steps",
    "resolve_step_class",
    "_import_class",
]
