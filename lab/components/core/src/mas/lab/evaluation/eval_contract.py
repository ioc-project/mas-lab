#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Evaluation Contract — LLM-free metric collection from observability events.

Position in the contract taxonomy
-----------------------------------
EvalContract is a CapabilityContract, orthogonal to the runtime.

Design principles
------------------
- App-agnostic interface (this file); app-specific logic in the app's
  ``evaluations/`` directory (e.g. ``labs/extensions.lab/extensions_lab/steps/eval_fact_recall.py``).
- Metrics not scores: plugins return named metric values (rates, counts,
  coverage fractions).  Aggregation into a scalar score is optional and done
  by the caller.
- Pluggable: callers use `mas-lab eval` or pipeline `eval_mce` / `eval_batch` steps.
  The UI evaluation pane subscribes to ``get_metrics()``.
- Real-time or post-hoc: ``on_event`` is the single ingest point for both
  streaming (called by the runtime after each ObservabilityPlugin emit) and
  batch replay (called by the JSONL reader in mas-lab).

Event format
-------------
The canonical event dict matches what EventRecorder writes and
ObservabilityPlugin emits:

    {"kind": "tool_call_end",  "agent_id": "telemetry",
     "tool_name": "get_metrics", "result": {...}, "timestamp": 1234.5}

    {"kind": "execution_start", "agent_id": "db", "timestamp": 1234.5}

    {"kind": "audit", "payload": {"agent_id": "planner",
     "task": {"prompt": "..."}}, "timestamp": 1234.5}

See ``mas.runtime.plugins.observability_plugin`` for the full event catalogue.
"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.runtime.contracts.base import BasePlugin  # L3 ← correct layer; not L4 core_plugins

# ---------------------------------------------------------------------------
# Shared plugin-class loader
# ---------------------------------------------------------------------------

def load_eval_plugin_class(spec: str, base_dir: Optional[Path] = None) -> type:
    """Load an :class:`EvalContract` subclass from a *spec* string.

    Accepts two formats:

    - **File path + class** ``/path/to/file.py::ClassName``
      Relative paths are resolved against *base_dir* (defaults to ``cwd``).
    - **Dotted module path** ``my.package.module.ClassName``
      Resolved via ``importlib.import_module``.

    Returns the **class** (not an instance).  The caller decides how to
    instantiate it (e.g. with a ``config`` dict).

    Raises :class:`ImportError` when the spec cannot be resolved.
    """
    if not spec:
        raise ImportError("eval_plugin spec is empty")

    if "::" in spec:
        file_part, class_name = spec.rsplit("::", 1)
        fp = Path(file_part.strip())
        if not fp.is_absolute():
            anchor = Path(base_dir) if base_dir is not None else Path.cwd()
            fp = anchor / fp
        loader_spec = importlib.util.spec_from_file_location("_eval_plugin", fp)
        if loader_spec is None or loader_spec.loader is None:
            raise ImportError(f"Cannot load file: {fp}")
        mod = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return getattr(mod, class_name.strip())
    else:
        module_path, class_name = spec.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)


# ---------------------------------------------------------------------------
# Metric descriptors
# ---------------------------------------------------------------------------

@dataclass
class MetricSpec:
    """Description of one metric emitted by an EvalContract plugin.

    Consumed by the mas-lab UI evaluation pane to render the right widget.
    """
    name: str
    description: str
    unit: str = ""           # e.g. "ratio", "count", "ms", "%"
    metric_type: str = "gauge"   # "gauge" | "rate" | "histogram" | "boolean"
    challenge: Optional[str] = None   # e.g. "C2" — links metric to a challenge


@dataclass
class MetricValue:
    """A single metric snapshot.

    ``value`` is always a float; booleans are 1.0/0.0.
    ``numerator`` / ``denominator`` are optionally exposed for rate metrics
    so the UI can display both the fraction and the raw counts.
    """
    name: str
    value: float
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    detail: str = ""         # human-readable explanation


# ---------------------------------------------------------------------------
# Contract base
# ---------------------------------------------------------------------------

class EvalContract(BasePlugin):
    """Abstract base for evaluation plugins.

    Subclass this in your use case's ``evaluations/`` directory.

    Lifecycle
    ---------
    1. Instantiate with optional ``config`` dict (app-specific parameters).
    2. Feed events via ``on_event(event)`` — one call per event dict.
    3. Call ``get_metrics()`` at any time (streaming) or at the end (batch).
    4. Call ``reset()`` to clear accumulated state for the next run.

    Thread safety
    -------------
    ``on_event`` may be called from different threads in streaming mode;
    subclasses are responsible for their own locking if needed.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self.config: Dict[str, Any] = config or {}

    # ------------------------------------------------------------------
    # Required interface
    # ------------------------------------------------------------------

    def on_event(self, event: Dict[str, Any]) -> None:
        """Ingest one observability event.

        Called for every event — ``kind`` discriminates the type.
        Implementors should update their internal metric state here.
        """
        raise NotImplementedError

    def get_metrics(self) -> Dict[str, MetricValue]:
        """Return a snapshot of all current metric values.

        Keys are metric names as declared in ``describe()``.
        Safe to call at any point; reflects state accumulated so far.
        """
        raise NotImplementedError

    def describe(self) -> List[MetricSpec]:
        """Declare the metrics this plugin emits.

        Called once at startup by the mas-lab UI to register display widgets.
        """
        raise NotImplementedError

    def reset(self) -> None:
        """Clear accumulated state — called between runs in batch mode."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Convenience: replay from file
    # ------------------------------------------------------------------

    def replay_jsonl(self, path: "Any") -> None:
        """Feed all events from a JSONL file into ``on_event``."""
        import json
        from pathlib import Path

        text = Path(path).read_text(encoding="utf-8", errors="replace")
        separator = "\n" if "\n" in text and text.count("\n") > 1 else r"\n"
        for line in text.split(separator):
            line = line.strip()
            if not line:
                continue
            try:
                self.on_event(json.loads(line))
            except Exception:
                pass
