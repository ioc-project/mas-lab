#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Uniform run context passed from lab scheduler to runtime plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID

OverlayRef = Union[str, Dict[str, Any]]

try:
    from mas.lab.inputs import RunInput
except ImportError:  # pragma: no cover - core importable without bench
    RunInput = Any  # type: ignore[misc, assignment]


@dataclass
class RunContext:
    """Everything a runtime plugin needs for one benchmark execution."""

    prompt: str
    config: Dict[str, Any]
    spec_path: Path
    output_dir: Path
    runner_id: str = DEFAULT_LAB_RUNNER_ID
    run_input: Optional[RunInput] = None
    flavour: Any = None
    run_seed: int = 0
    overlay_refs: List[OverlayRef] = field(default_factory=list)
    overlays_dir: Optional[Path] = None
    overlay_base_dir: Optional[Path] = None
    infra_refs: List[str] = field(default_factory=list)
    session_id: Optional[str] = None
    emulation_plugins: Optional[List[Any]] = None
    batch_cache: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def runner_kwargs(self) -> Dict[str, Any]:
        """Keyword arguments for :meth:`ApplicationRunnerProtocol.run`."""
        kw: Dict[str, Any] = {
            "config": self.config,
            "spec_path": self.spec_path,
            "flavour": self.flavour,
            "output_dir": self.output_dir,
            "run_input": self.run_input,
            "session_id": self.session_id
            if self.session_id is not None
            else (self.run_input.session_id if self.run_input is not None else None),
            "run_seed": self.run_seed,
            "overlay_refs": self.overlay_refs,
            "overlays_dir": self.overlays_dir,
            "overlay_base_dir": self.overlay_base_dir,
            "infra_refs": self.infra_refs,
            "emulation_plugins": self.emulation_plugins,
            "batch_cache": self.batch_cache,
        }
        kw.update(self.extra)
        required = {"config", "spec_path", "flavour", "output_dir"}
        return {k: v for k, v in kw.items() if k in required or v is not None}
