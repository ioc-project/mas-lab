#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""mas-lab pipeline engine — processor chaining.

This module implements the data pipeline model:

- Each **element** is a registered :class:`~mas.lab.processor.Processor`.
- Elements are connected by their artifact types (``output_kind`` → ``input_kind``).
- Parameters are passed as ``key=value`` tokens next to the element name.
- The pipeline runs **in-process**: no subprocesses, full type safety.

Inline syntax
~~~~~~~~~~~~~
::

    mas-lab pipe run trajectory_loader trace=runs/.../events.jsonl  !  multilevel_trajectory_plotter fmt=html
    mas-lab pipe run trajectory_loader trace=runs/.../events.jsonl  !  trajectory_plotter_native fmt=svg

The ``!`` token separates elements.  Parameters use ``key=value`` (coerced
to int/float/bool automatically).  Order is positional when a processor has
a single required parameter with no name match (same as ``mas-lab run processor``).

YAML pipeline file
~~~~~~~~~~~~~~~~~~
Save a pipeline to a ``.pipeline.yaml`` file::

    version: "1"
    steps:
      - element: trajectory_loader
        params:
          trace: "runs/.../traces/events.jsonl"
      - element: multilevel_trajectory_plotter
        params:
          fmt: html

Run with::

    mas-lab pipe run --file my-pipeline.yaml
    mas-lab pipe run --file my-pipeline.yaml --dry-run

GUI-compatible schema
~~~~~~~~~~~~~~~~~~~~~
::

    mas-lab pipe schema                          # all elements
    mas-lab pipe schema --element gantt-plot     # one element
    mas-lab pipe schema --output schema.json     # to file

The JSON output follows the node-graph format used by n8n / Blender::

    {
      "version": "1",
      "elements": [
        {
          "id": "gantt-plot",
          "description": "...",
          "inputs":  [{ "name": "spans", "type": "ndjson/trajectory", "required": true }],
          "outputs": [],
          "params":  [
            { "name": "width",  "type": "int",  "default": 60,   "description": "..." },
            { "name": "levels", "type": "str",  "required": false, "description": "..." }
          ]
        }
      ]
    }

A saved pipeline as a graph (for n8n-style GUI)::

    {
      "version": "1",
      "nodes": [
        { "id": "n0", "element": "graph-dump", "params": {"session_id": "..."} },
        { "id": "n1", "element": "gantt-plot", "params": {"width": 80} }
      ],
      "edges": [
        { "from": "n0", "to": "n1", "from_slot": "output", "to_slot": "input" }
      ]
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PIPELINE_VERSION = "1"


# ---------------------------------------------------------------------------
# Pipeline data model
# ---------------------------------------------------------------------------

@dataclass
class PipelineStep:
    """One element in a pipeline with its resolved parameters.

    Attributes
    ----------
    element : Processor name (e.g. ``"gantt-plot"``).
    params  : Key-value parameters passed to ``Processor.process(**params)``.
    """

    element: str
    params:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"element": self.element, "params": dict(self.params)}


@dataclass
class Pipeline:
    """An ordered sequence of :class:`PipelineStep` instances.

    Use the factory class methods to construct from inline tokens, a YAML
    file, or a raw dict (e.g. deserialized from a GUI).
    """

    steps:   list[PipelineStep]
    version: str = PIPELINE_VERSION
    name:    str = ""
    description: str = ""

    # ── factory: inline token stream ─────────────────────────────────────────

    @classmethod
    def from_tokens(cls, tokens: list[str]) -> "Pipeline":
        """Build from a flat token list split on ``!``.

        ::

            from_tokens([
                "graph-dump", "session_id=xyz", "!",
                "gantt-plot", "width=80",
            ])
        """
        segments = _split_on_bang(tokens)
        steps = [_parse_segment(seg) for seg in segments]
        return cls(steps=steps)

    @classmethod
    def from_inline(cls, s: str) -> "Pipeline":
        """Build from an inline pipeline string.

        ::

            from_inline("graph-dump session_id=xyz ! gantt-plot width=80")
        """
        return cls.from_tokens(s.split())

    # ── factory: YAML / dict ─────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, d: dict) -> "Pipeline":
        """Build from a deserialized YAML/JSON dict."""
        steps = [
            PipelineStep(
                element=step["element"],
                params={str(k): v for k, v in step.get("params", {}).items()},
            )
            for step in d.get("steps", [])
        ]
        return cls(
            steps=steps,
            version=str(d.get("version", PIPELINE_VERSION)),
            name=d.get("name", ""),
            description=d.get("description", ""),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "Pipeline":
        """Load from a ``.pipeline.yaml`` file."""
        from mas.runtime.spec.source import load_yaml_file

        raw = load_yaml_file(path)
        return cls.from_dict(raw if isinstance(raw, dict) else {})

    # ── serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to a dict (YAML-dumpable, GUI-friendly)."""
        d: dict = {
            "version": self.version,
            "steps":   [s.to_dict() for s in self.steps],
        }
        if self.name:
            d["name"] = self.name
        if self.description:
            d["description"] = self.description
        return d

    def to_graph_dict(self) -> dict:
        """Serialize as a node-graph (n8n / Blender compatible).

        Returns a dict with ``nodes`` and ``edges`` arrays.  Each node has a
        stable ``id`` (``n0``, ``n1``, …) so a GUI can reference it.
        """
        nodes = []
        edges = []
        for i, step in enumerate(self.steps):
            nodes.append({
                "id":      f"n{i}",
                "element": step.element,
                "params":  dict(step.params),
            })
            if i > 0:
                edges.append({
                    "from":       f"n{i - 1}",
                    "to":         f"n{i}",
                    "from_slot":  "output",
                    "to_slot":    "input",
                })
        return {
            "version": self.version,
            "nodes":   nodes,
            "edges":   edges,
        }

    def to_yaml(self, path: Path) -> None:
        """Write the pipeline to a YAML file."""
        try:
            import yaml as _yaml  # type: ignore
        except ImportError as exc:
            raise ImportError("PyYAML required.  uv add pyyaml") from exc
        with open(path, "w", encoding="utf-8") as fh:
            _yaml.dump(self.to_dict(), fh, default_flow_style=False, allow_unicode=True)

    def to_inline(self) -> str:
        """Return the pipeline as an inline string."""
        parts = []
        for step in self.steps:
            tokens = [step.element]
            tokens += [f"{k}={v}" for k, v in step.params.items()]
            parts.append(" ".join(tokens))
        return " ! ".join(parts)

    # ── validation ───────────────────────────────────────────────────────────

    def validate(self, strict: bool = False) -> list[str]:
        """Check element names and parameter compatibility.

        Returns a list of issue strings.  Empty list means valid.
        When *strict* is True, also verify ``output_kind → input_kind`` compatibility.
        """
        from mas.lab.processor import get_processor

        issues: list[str] = []
        if not self.steps:
            issues.append("Pipeline has no steps.")
            return issues

        prev_output_kind: str | None = None
        for i, step in enumerate(self.steps):
            # Resolve element
            try:
                proc_cls = get_processor(step.element)
            except KeyError as exc:
                issues.append(f"Step {i}: {exc}")
                prev_output_kind = None
                continue

            # Check required params
            from mas.lab.processor import ParamDef
            for p in getattr(proc_cls, "params", []):
                if isinstance(p, ParamDef) and p.required and p.name not in step.params:
                    issues.append(
                        f"Step {i} ({step.element}): required param {p.name!r} missing."
                    )

            # Slot compatibility
            if strict and prev_output_kind is not None:
                input_kind = getattr(proc_cls, "input_kind", "")
                if input_kind and prev_output_kind and input_kind != prev_output_kind:
                    issues.append(
                        f"Step {i} ({step.element}): "
                        f"expects input_kind={input_kind!r} but previous step "
                        f"outputs {prev_output_kind!r}."
                    )

            prev_output_kind = getattr(proc_cls, "output_kind", "") or None

        return issues

    # ── execution ────────────────────────────────────────────────────────────

    def run(
        self,
        *,
        verbose: bool = False,
        dry_run: bool = False,
    ) -> Any:
        """Execute the pipeline in-process.

        Each step receives the artifact produced by the previous step.  The
        first step receives ``None`` (source processors don't need an input).

        Returns the final artifact (or ``None`` in dry-run mode).
        """
        from mas.lab.processor import get_processor

        if dry_run:
            import click
            for i, step in enumerate(self.steps):
                try:
                    proc_cls = get_processor(step.element)
                except KeyError:
                    proc_cls = None
                name     = proc_cls.name if proc_cls else f"<unknown:{step.element}>"
                desc     = proc_cls.description if proc_cls else "NOT FOUND"
                in_kind  = getattr(proc_cls, "input_kind",  "—") if proc_cls else "—"
                out_kind = getattr(proc_cls, "output_kind", "—") if proc_cls else "—"
                params_str = ", ".join(f"{k}={v!r}" for k, v in step.params.items()) or "(none)"
                click.echo(
                    f"  [{i}] {name:<30}  {in_kind} → {out_kind}\n"
                    f"       {desc}\n"
                    f"       params: {params_str}"
                )
            return None

        artifact = None
        for i, step in enumerate(self.steps):
            proc_cls = get_processor(step.element)
            # Merge ParamDef defaults with user params
            resolved = _resolve_params(proc_cls, step.params)
            proc     = proc_cls()
            if verbose:
                import click
                click.echo(
                    f"  [{i}] {step.element}  params={resolved}",
                    err=True,
                )
            artifact = proc.process(artifact, **resolved)

        return artifact


# ---------------------------------------------------------------------------
# Schema export (GUI-compatible)
# ---------------------------------------------------------------------------

def build_schema(element: str | None = None) -> dict:
    """Build a JSON-compatible schema dict for GUI consumers.

    When *element* is given, returns the schema for that processor only.
    Otherwise returns a full registry schema.
    """
    from mas.lab.processor import ProcessorManifest, get_processor, list_processors

    # Ensure built-ins are loaded
    try:
        import mas.lab.plots as _  # noqa: F401
    except Exception:
        pass

    if element:
        proc_cls = get_processor(element)
        manifest = ProcessorManifest.from_processor_cls(proc_cls)
        return {"version": PIPELINE_VERSION, "elements": [manifest.to_schema_dict()]}

    elements = []
    for proc_cls in list_processors():
        manifest = ProcessorManifest.from_processor_cls(proc_cls)
        elements.append(manifest.to_schema_dict())

    return {"version": PIPELINE_VERSION, "elements": elements}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_on_bang(tokens: list[str]) -> list[list[str]]:
    """Split a flat token list on ``!`` into segments.

    Rejects empty segments (degenerate ``! !`` or leading/trailing ``!``).
    """
    if not tokens:
        raise ValueError("Empty pipeline token list.")

    segments: list[list[str]] = []
    current: list[str] = []
    for t in tokens:
        if t == "!":
            if not current:
                raise ValueError(
                    "Empty segment before '!' — check for leading or double '!' tokens."
                )
            segments.append(current)
            current = []
        else:
            current.append(t)

    if not current:
        raise ValueError("Trailing '!' — pipeline must not end with '!'.")
    segments.append(current)

    if len(segments) < 1:
        raise ValueError("Pipeline must contain at least one step.")

    return segments


def _parse_segment(tokens: list[str]) -> PipelineStep:
    """Parse ``["element-name", "key=val", ...]`` into a :class:`PipelineStep`."""
    if not tokens:
        raise ValueError("Empty segment in pipeline.")

    element = tokens[0]
    params: dict[str, Any] = {}

    for tok in tokens[1:]:
        if "=" in tok:
            key, _, val = tok.partition("=")
            params[key.strip()] = _coerce(val)
        else:
            # Positional: assign to the first required param with no default
            _assign_positional(element, params, tok)

    return PipelineStep(element=element, params=params)


def _assign_positional(element: str, params: dict, value: str) -> None:
    """Assign a positional token to the first unnamed required param."""
    try:
        from mas.lab.processor import ParamDef, get_processor
        proc_cls = get_processor(element)
        for p in getattr(proc_cls, "params", []):
            if isinstance(p, ParamDef) and p.name not in params:
                params[p.name] = _coerce(value)
                return
    except Exception:
        pass
    # Fallback: use generic key
    idx = sum(1 for k in params if k.startswith("_arg"))
    params[f"_arg{idx}"] = _coerce(value)


def _resolve_params(proc_cls: type, user_params: dict[str, Any]) -> dict[str, Any]:
    """Merge ParamDef defaults with user-supplied params."""
    from mas.lab.processor import ParamDef
    resolved = {}
    for p in getattr(proc_cls, "params", []):
        if isinstance(p, ParamDef) and p.default is not None and p.name not in user_params:
            resolved[p.name] = p.default
    resolved.update(user_params)
    return resolved


def _coerce(val: str) -> Any:
    """Parse a string token to int / float / bool / str."""
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val
