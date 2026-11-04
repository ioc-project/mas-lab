#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Processor protocol and global registry for the mas-lab processor system.

A :class:`Processor` is the atomic reusable building block of mas-lab:

* **one Artifact in, one Artifact out** — pure, composable, testable
* **three usage modes**:

  1. Direct Python API: ``plotter.process(trajectory)``
  2. Pipeline step YAML: ``type: processor, processor: trajectory_plotter``
  3. CLI: ``mas-lab run processor trajectory_plotter trace=<run_id> plot=out.svg plot.format=svg``

Registering a processor::

    from mas.lab.processor import Processor, register

    @register
    class MyProcessor(Processor):
        name        = "my_processor"
        input_kind  = "trajectory"
        output_kind = "plot_file"
        description = "Demo processor"

        def process(self, artifact, **kwargs):
            ...

Discovery::

    from mas.lab.processor import list_processors, get_processor

    for p in list_processors():
        print(p.describe())

    cls = get_processor("trajectory_plotter")

Manifest
--------
Each processor may ship a YAML manifest (same filename, ``.yaml`` extension)
that declares its named input/output artifacts and their default attributes::

    name: trajectory_plotter
    priority: 1
    inputs:
      - name: trace
        kind: trajectory
    outputs:
      - name: plot
        kind: plot_file
        defaults:
          format: html

CLI syntax derived from the manifest::

    mas-lab run processor trajectory_plotter \\
        trace=20260224-140201-baseline-e60feafd \\
        plot=output/traj.svg \\
        plot.format=svg
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Type

if TYPE_CHECKING:
    from mas.lab.artifacts import Artifact


# ---------------------------------------------------------------------------
# Parameter schema (GUI, pipe, plot processors)
# ---------------------------------------------------------------------------

@dataclass
class ParamDef:
    """Declares one parameter accepted by a :class:`Processor`."""

    name: str
    type: str = "str"
    default: Any = None
    required: bool = False
    description: str = ""
    choices: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.default is not None:
            d["default"] = self.default
        if self.choices:
            d["choices"] = self.choices
        return d


# ---------------------------------------------------------------------------
# Manifest dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ArtifactSlot:
    """A named input or output artifact slot declared in a processor manifest."""
    name: str
    kind: str
    description: str = ""
    defaults: Dict[str, Any] = field(default_factory=dict)
    required: bool = True


@dataclass
class ProcessorManifest:
    """Parsed representation of a processor's YAML manifest."""
    name: str
    priority: int = 10
    description: str = ""
    inputs: List[ArtifactSlot] = field(default_factory=list)
    outputs: List[ArtifactSlot] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "ProcessorManifest":
        import yaml  # soft dep — always available (pyyaml bundled in mas-lab)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        inputs = [
            ArtifactSlot(
                name=s["name"],
                kind=s["kind"],
                description=s.get("description", ""),
                defaults=s.get("defaults", {}),
                required=s.get("required", True),
            )
            for s in data.get("inputs", [])
        ]
        outputs = [
            ArtifactSlot(
                name=s["name"],
                kind=s["kind"],
                description=s.get("description", ""),
                defaults=s.get("defaults", {}),
                required=s.get("required", True),
            )
            for s in data.get("outputs", [])
        ]
        return cls(
            name=data.get("name", ""),
            priority=data.get("priority", 10),
            description=data.get("description", ""),
            inputs=inputs,
            outputs=outputs,
        )

    @classmethod
    def from_processor_cls(cls, proc_cls: Type["Processor"]) -> "ProcessorManifest":
        """Load the manifest from the YAML file next to the processor's source."""
        import inspect
        src = inspect.getfile(proc_cls)
        yaml_path = Path(src).with_suffix(".yaml")
        if yaml_path.exists():
            return cls.from_yaml(yaml_path)
        # Fallback: synthesise a minimal manifest from class attributes
        return cls(
            name=proc_cls.name,
            priority=proc_cls.priority,
            description=proc_cls.description,
            inputs=[ArtifactSlot(name="input", kind=proc_cls.input_kind)],
            outputs=[ArtifactSlot(name="output", kind=proc_cls.output_kind)],
        )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class Processor(ABC):
    """Atomic transform: one :class:`~mas.lab.artifacts.Artifact` → one Artifact.

    Subclasses **must** declare three class-level attributes:

    * ``name``         — unique kebab-case identifier (used in YAML & CLI)
    * ``input_kind``   — the ``Artifact.kind`` this processor accepts
    * ``output_kind``  — the ``Artifact.kind`` this processor produces

    And implement :meth:`process`.
    """

    name:        ClassVar[str]
    input_kind:  ClassVar[str]
    output_kind: ClassVar[str]
    description: ClassVar[str] = ""
    priority:    ClassVar[int] = 10  # lower number = more preferred (1 = default choice)
    params:      ClassVar[list[ParamDef]] = []

    @abstractmethod
    def process(self, artifact: "Artifact", **kwargs: Any) -> "Artifact":
        """Transform *artifact* into a new artifact.

        Parameters
        ----------
        artifact:
            Input artifact.  Its ``kind`` should match ``self.input_kind``,
            but processors may also accept subclasses (e.g. a processor that
            accepts ``trajectory`` also accepts ``annotated_trajectory``).
        **kwargs:
            Processor-specific options forwarded from pipeline config or CLI.

        Returns
        -------
        Artifact
            New artifact whose ``kind`` matches ``self.output_kind``.
        """

    def cli_options(self) -> List[Dict[str, Any]]:
        """Declare extra Click options for this processor's CLI invocation.

        Override to expose processor-specific flags.

        Returns
        -------
        list of dicts
            Each dict is passed verbatim as ``**kwargs`` to ``click.option``.
            The ``"param_decls"`` key holds the flag name(s).

        Example::

            def cli_options(self):
                return [
                    {"param_decls": ["--format", "-f"],
                     "default": "html",
                     "help": "Output format."},
                    {"param_decls": ["--highlight"],
                     "multiple": True,
                     "help": "Mark a delegation (corr-id or index)."},
                ]
        """
        return []

    @classmethod
    def describe(cls) -> str:
        """One-line human-readable summary."""
        return f"{cls.name:<32} {cls.input_kind:<24} → {cls.output_kind:<24}  {cls.description}"

    def __repr__(self) -> str:
        return f"<Processor {self.__class__.name}>"


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Type[Processor]] = {}
_DEFAULTS_LOADED: bool = False


def register(cls: Type[Processor]) -> Type[Processor]:
    """Class decorator — register *cls* in the global processor registry.

    Usage::

        @register
        class MyProcessor(Processor):
            name = "my_processor"
            ...
    """
    if not hasattr(cls, "name") or not cls.name:
        raise TypeError(f"{cls.__qualname__}: must define a non-empty 'name' class variable")
    _REGISTRY[cls.name] = cls
    return cls


def get_processor(name: str) -> Type[Processor]:
    """Return the processor class registered under *name*.

    Raises
    ------
    KeyError
        If no processor with that name is registered.
    """
    _ensure_defaults_loaded()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown processor '{name}'. "
            f"Available: {', '.join(sorted(_REGISTRY)) or '(none registered)'}"
        )
    return _REGISTRY[name]


def list_processors() -> List[Type[Processor]]:
    """Return all registered processor classes sorted by name."""
    _ensure_defaults_loaded()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def _ensure_defaults_loaded() -> None:
    """Import the default processor bundle (mas.lab.processors) if not yet done.

    This is a lazy import so that lab/components/core does not hard-depend on
    lab/components/bench.  The import is idempotent.  Uses a dedicated flag instead
    of checking ``_REGISTRY`` — other sub-packages (e.g. mas.lab.plots) may
    have already registered processors before this is called, which would
    cause the registry to appear non-empty and skip the import.
    """
    global _DEFAULTS_LOADED
    if not _DEFAULTS_LOADED:
        _DEFAULTS_LOADED = True
        try:
            import mas.lab.processors  # noqa: F401  triggers @register calls
        except ImportError:
            pass  # no processors installed — caller will get an empty list
