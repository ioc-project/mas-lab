#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Select which observability layers to export as OTel spans.

SCOPE's required class layers are structure, execution, and trajectory
(``provenance`` here). Semantic (context) stays on with that default set.
Governance is opt-in.

The layer of an event is taken from its explicit ``layer`` field when present,
otherwise derived from its ``kind`` via
:func:`mas.library.telemetry.conversion.envelope.export_layer_for_kind`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from mas.library.telemetry.conversion.envelope import export_layer_for_kind


@dataclass(frozen=True)
class ExportLayers:
    """Layer toggles for OTel export (converter and plugins).

    Attributes:
        structure:  structural spans (Session/Agent/Task tree). Default on.
        execution:  execution spans (LLM/Tool/Memory/…). Default on.
        semantic:   context/state annotations. Default on.
        provenance: trajectory spans (parallel groups, branches, wait). Default on.
        governance: governance / policy / HITL spans. Default off.
    """

    structure: bool = True
    execution: bool = True
    semantic: bool = True
    provenance: bool = True
    governance: bool = False

    @classmethod
    def oxp_default(cls) -> "ExportLayers":
        """SCOPE-required layers: structure, execution, trajectory (plus semantic)."""
        return cls()

    @classmethod
    def complete(cls) -> "ExportLayers":
        """Every layer, including governance."""
        return cls(governance=True)

    def enabled(self, layer: str) -> bool:
        return bool(getattr(self, layer, True))

    def to_dict(self) -> Dict[str, bool]:
        return {
            "structure": self.structure,
            "execution": self.execution,
            "semantic": self.semantic,
            "provenance": self.provenance,
            "governance": self.governance,
        }


def parse_export_layers(cfg: Dict[str, Any] | None) -> ExportLayers:
    """Parse layer flags from a plugin/step manifest config dict."""
    cfg = cfg or {}
    layers_cfg = cfg.get("export_layers")
    if isinstance(layers_cfg, dict):
        cfg = {**cfg, **layers_cfg}
    return ExportLayers(
        structure=_flag(cfg, "structure", "structural", default=True),
        execution=_flag(cfg, "execution", default=True),
        semantic=_flag(cfg, "semantic", "context", default=True),
        provenance=_flag(cfg, "provenance", "trajectory", default=True),
        governance=_flag(cfg, "governance", default=False),
    )


def _flag(
    cfg: Dict[str, Any], primary: str, alias: str | None = None, *, default: bool
) -> bool:
    if primary in cfg:
        return bool(cfg[primary])
    if alias is not None and alias in cfg:
        return bool(cfg[alias])
    return default


def should_export_event(event: Dict[str, Any], layers: ExportLayers) -> bool:
    """Return whether *event* should be converted to OTel spans under *layers*."""
    kind = str(event.get("kind") or "")
    layer = str(event.get("layer") or "") or export_layer_for_kind(kind)
    if not layer:
        return True
    return layers.enabled(layer)


def layer_for_kind(kind: str) -> str | None:
    """Map a native event kind to an export layer name."""
    return export_layer_for_kind(kind)


__all__ = [
    "ExportLayers",
    "layer_for_kind",
    "parse_export_layers",
    "should_export_event",
]
