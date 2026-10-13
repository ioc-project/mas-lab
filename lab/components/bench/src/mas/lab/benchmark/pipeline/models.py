#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
"""
Pipeline data models and manifest descriptors.
"""


from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

#: Keys accepted at the step level in a pipeline YAML.
_STEP_KNOWN_KEYS: frozenset = frozenset({
    "name", "type", "config", "depends_on", "description", "persist", "phase",
    "per_scenario",
    "per_run",
    "concurrency",
    "in",
    "out",
    "scope",
    "role",
    "resource",
    "refresh",
    "batch",
})

#: Keys accepted at the pipeline-config level.
_CONFIG_KNOWN_KEYS: frozenset = frozenset({
    "name", "version", "description", "output", "steps", "max_active",
})

_MISSING = object()  # sentinel for "no default"


@dataclass
class ConfigParam:
    """Declarative descriptor for a single config key of a pipeline step."""

    name: str
    type: type = str
    default: Any = _MISSING
    description: str = ""

    @property
    def required(self) -> bool:
        return self.default is _MISSING

    def default_repr(self) -> str:
        if self.default is _MISSING:
            return "(required)"
        if self.default is None:
            return "null"
        return repr(self.default)


@dataclass
class StepOutput:
    """Output from a pipeline step execution."""

    data: Dict[str, Any] = field(default_factory=dict)
    files: List[Path] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class PipelineConfig:
    """Pipeline-wide configuration."""

    name: str
    version: str = "v1"
    description: str = ""
    output: Dict[str, Any] = field(default_factory=lambda: {"base_dir": "./output"})
    max_active: int = 8

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PipelineConfig:
        return cls(
            name=data["name"],
            version=data.get("version", "v1"),
            description=data.get("description", ""),
            output=data.get("output", {"base_dir": "./output"}),
            max_active=max(1, int(data.get("max_active") or 8)),
        )


@dataclass
class ArtifactSpec:
    """Describes a single input or output artifact of a pipeline step."""

    name: str
    kind: str
    description: str = ""
    file_pattern: str = ""
    from_dependency: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ArtifactSpec:
        return cls(
            name=d["name"],
            kind=d.get("kind", d.get("type", "data")),
            description=d.get("description", ""),
            file_pattern=d.get("file_pattern", d.get("file", "")),
            from_dependency=bool(d.get("from_dependency", False)),
        )


@dataclass
class StepManifest:
    """Declarative description of a pipeline step — its inputs, outputs, and config."""

    step_type: str
    description: str
    inputs: List[ArtifactSpec] = field(default_factory=list)
    outputs: List[ArtifactSpec] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> StepManifest:
        # Local YAML load — the framework base must not depend on mas.runtime.
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: step manifest must be a YAML mapping")
        step = raw.get("step", raw)
        return cls(
            step_type=step["type"],
            description=step.get("description", ""),
            inputs=[ArtifactSpec.from_dict(a) for a in step.get("inputs", [])],
            outputs=[ArtifactSpec.from_dict(a) for a in step.get("outputs", [])],
            config_schema=step.get("config", {}),
        )

    def describe(self) -> str:
        lines = [f"Step: {self.step_type}", f"  {self.description}", ""]
        if self.inputs:
            lines.append("  Inputs:")
            for a in self.inputs:
                req = "optional" if "optional" in a.kind else "required"
                lines.append(f"    {a.name} ({a.kind}, {req}): {a.description}")
        if self.outputs:
            lines.append("  Outputs:")
            for a in self.outputs:
                suffix = f"  [{a.file_pattern}]" if a.file_pattern else ""
                lines.append(f"    {a.name} ({a.kind}){suffix}: {a.description}")
        return "\n".join(lines)


__all__ = [
    "_MISSING",
    "_STEP_KNOWN_KEYS",
    "_CONFIG_KNOWN_KEYS",
    "ConfigParam",
    "StepOutput",
    "PipelineConfig",
    "ArtifactSpec",
    "StepManifest",
]
