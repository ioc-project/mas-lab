#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mas.runtime.spec.source import resolve_path as resolve_path_ref


@dataclass
class OverlayStack:
    """Layered overlay stacks — logic, control, infra applied in that order."""

    logic: "List[Union[str, dict]]" = field(default_factory=list)
    control: "List[Union[str, dict]]" = field(default_factory=list)
    infra: "List[Union[str, dict]]" = field(default_factory=list)

    def flattened(self) -> "List[Union[str, dict]]":
        return list(self.logic) + list(self.control) + list(self.infra)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, scenario_id: str) -> "OverlayStack":
        if not isinstance(data, dict):
            raise ValueError(f"scenario {scenario_id}: overlays must be {{logic, control, infra}}")
        logic = list(data.get("logic") or [])
        control = list(data.get("control") or [])
        infra = list(data.get("infra") or [])
        return cls(logic=logic, control=control, infra=infra)


@dataclass
class MASScenarioSpec:
    """One selectable scenario — shared by LabConfig and MASExperimentConfig.

    Parallel to a *variant* in ExperimentConfig: it names one execution
    context for the MAS, optionally overlaying extra configuration on top of
    the base JSON config.
    """

    id: str
    """Scenario ID shown in reports and used as run directory name."""

    description: str = ""
    """Human-readable label shown in the UI dropdown or benchmark report."""

    version: Optional[int] = None
    """Scenario version — tracks overlay iteration history.

    When set, the output directory for this scenario is named
    ``{id}.v{version}`` instead of plain ``{id}``, so that runs from
    different overlay iterations coexist under the same experiment and can be
    compared directly.

    Declare this in the experiment YAML when an overlay has been deliberately
    revised (e.g.  after a new suppression technique is found)::

        scenarios:
          - id: c7-missing-evidence
            version: 2
            description: "Revised — stronger suppression via explicit negative"
          - id: baseline

    Omit ``version`` (or set ``version: 1``) for the first iteration of a
    scenario; increment it whenever the overlay definition changes in a way
    that would invalidate comparison with previous runs.
    """

    overlays: OverlayStack = field(default_factory=OverlayStack)
    """Layered overlay stacks applied logic → control → infra on mas.yaml."""

    inputs: Dict[str, Any] = field(default_factory=dict)
    """Optional scenario-level default inputs (merged into RunInput)."""

    expectations: Dict[str, Any] = field(default_factory=dict)
    """Optional scenario-level expectations (merged into RunInput)."""

    tags: List[str] = field(default_factory=list)
    """Optional labels for grouping / filtering (e.g. ["challenge", "hard"])."""

    user_prompt: str = ""
    """Optional default prompt pre-filled in the topbar when this scenario is selected."""

    flavour: Optional[str] = None
    """Optional per-scenario flavour override.

    When set, this scenario uses the named flavour file instead of the
    experiment-level ``default_flavour``.  Resolved from the same
    search paths (experiment-local ``flavours/``, workspace root, bundled).
    """

    environment: Dict[str, Any] = field(default_factory=dict)
    """Scenario-level ``environment:`` merged over the experiment block."""

    depends_on: List[str] = field(default_factory=list)
    """Producer scenario ids. A ``resume_from`` consumer waits for that
    published checkpoint file (not the full producer run). Other
    dependencies wait until the producer finishes. The consumer takes a
    ``parallel_scenarios`` slot only when it runs.
    """

    resume_from: Optional[str] = None
    """Named checkpoint this scenario loads (producer ``produce_checkpoints``)."""

    resume_index: int = 1
    """1-based occurrence when the producer trigger fired more than once."""

    produce_checkpoints: List[str] = field(default_factory=list)
    """Checkpoint ids this scenario publishes (``experiment.session_checkpoints``)."""

    debug: Optional[Dict[str, Any]] = None
    """Structured checkpoint/pause trigger, or a raw gdb ``script`` override."""

    pipeline_resources: List[Dict[str, Any]] = field(default_factory=list)
    """Scoped resource declarations local to this scenario.

    Resources declared here are visible only to pipeline steps executing
    within this scenario (or narrower scopes).  They are merged with
    experiment-level ``pipeline_resources`` at execution time; a
    scenario-level resource with the same name overrides the
    experiment-level one.
    """

    @property
    def output_dir_name(self) -> str:
        """Return the directory name to use for this scenario's outputs.

        Returns ``{id}.v{version}`` when *version* is set, plain ``{id}``
        otherwise (backward-compatible for unversioned scenarios).
        """
        if self.version is not None:
            return f"{self.id}.v{self.version}"
        return self.id

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: Path) -> "MASScenarioSpec":
        raw_overlays = data.get("overlays")
        if raw_overlays is None:
            overlay_stack = OverlayStack(logic=[data["id"]])
        else:
            overlay_stack = OverlayStack.from_dict(raw_overlays, scenario_id=data["id"])
        raw_version = data.get("version")
        version: Optional[int] = int(raw_version) if raw_version is not None else None
        resume_from, resume_index = _parse_resume_from(data.get("resume_from"), data.get("resume_index"))
        return cls(
            id=data["id"],
            description=data.get("description", ""),
            version=version,
            overlays=overlay_stack,
            inputs=dict(data.get("inputs") or {}),
            expectations=dict(data.get("expectations") or {}),
            flavour=data.get("flavour"),
            tags=data.get("tags", []),
            user_prompt=data.get("user_prompt", ""),
            pipeline_resources=data.get("pipeline_resources", []),
            environment=dict(data.get("environment") or {}),
            depends_on=_str_list(data.get("depends_on"), "depends_on"),
            resume_from=resume_from,
            resume_index=resume_index,
            produce_checkpoints=_str_list(data.get("produce_checkpoints"), "produce_checkpoints"),
            debug=_debug_block(data.get("debug")),
        )


def _str_list(raw: object, name: str) -> list[str]:
    """A list of names; a bare string is one name (not its characters)."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    raise ValueError(f"{name} must be a name or a list of names, got {type(raw).__name__}")


def _debug_block(raw: object) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"debug must be a mapping, got {type(raw).__name__}")
    return dict(raw)


def _positive_index(raw: object, name: str) -> int:
    if raw is None or raw == "":
        return 1
    if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).strip().lstrip("+").isdigit():
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    value = int(str(raw).strip())
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {raw!r}")
    return value


def _parse_resume_from(raw: object, resume_index: object) -> tuple[Optional[str], int]:
    index = _positive_index(resume_index, "resume_index")
    if raw is None or raw == "":
        return None, index
    if isinstance(raw, str):
        return raw, index
    if isinstance(raw, dict):
        cid = str(raw.get("id") or "").strip() or None
        nested = raw.get("index", raw.get("occurrence"))
        if nested is not None:
            index = _positive_index(nested, "resume_from.index")
        return cid, index
    raise ValueError("resume_from must be a name or {id, index}")


@dataclass
class MASSpec:
    """Pointer to a MAS use-case (parallel to AgentSpec for single agents).

    A MAS use-case is a directory holding named scenario files.  Supports two
    resolution strategies:

    * **manifest** (preferred):  ``manifest: path/to/mas.yaml`` — overlays are
      loaded from ``<manifest_parent>/overlays/``.  Preferred for unified
      ``experiment.yaml`` files where scenarios declare
      ``overlays: [list]``.
    * **configs_dir**:  ``configs_dir: path/to/overlays/`` — directory of
      scenario overlay YAML files applied on top of the sibling ``mas.yaml``.
    """

    configs_dir: Optional[Path] = None
    """Absolute path to the scenario overlay directory (typically ``overlays/``)."""

    manifest: Optional[Path] = None
    """Absolute path to the MAS ``mas.yaml`` manifest.

    When set, ``configs_dir`` defaults to ``manifest.parent / "overlays"``
    for auto-discovery and backward-compat helpers.
    """

    base_scenario: str = "baseline"
    """Default scenario to load on startup."""

    @property
    def effective_configs_dir(self) -> Optional[Path]:
        """Return configs_dir or derive it from the manifest parent."""
        if self.configs_dir:
            return self.configs_dir
        if self.manifest:
            return self.manifest.parent / "overlays"
        return None

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | str, base_dir: Path) -> "MASSpec":
        if isinstance(data, str):
            data = {"app": data.strip()}
        # ── app: <library:app>  (preferred — catalog id, type inferred) ──────
        if "app" in data:
            from mas.apps import get_app, resolve_app_manifest

            app_root = get_app(data["app"])
            manifest = resolve_app_manifest(app_root, app_id=str(data["app"]))
            configs_dir: Optional[Path] = None
            if "configs_dir" in data:
                configs_dir = resolve_path_ref(str(data["configs_dir"]), base_dir)
            return cls(
                manifest=manifest,
                configs_dir=configs_dir,
                base_scenario=data.get("base_scenario", "baseline"),
            )
        # ── manifest: path or library:app ────────────────────────────────────
        if "manifest" in data:
            ref = str(data["manifest"])
            manifest = resolve_path_ref(ref, base_dir)
            if not manifest.is_file():
                from mas.apps import get_app, resolve_app_manifest

                if manifest.is_dir():
                    manifest = resolve_app_manifest(manifest)
                else:
                    try:
                        manifest = resolve_app_manifest(get_app(ref))
                    except Exception:
                        pass
            configs_dir = None
            if "configs_dir" in data:
                configs_dir = resolve_path_ref(str(data["configs_dir"]), base_dir)
            return cls(
                manifest=manifest,
                configs_dir=configs_dir,
                base_scenario=data.get("base_scenario", "baseline"),
            )
        # ── configs_dir: path/  (legacy) ─────────────────────────────────────
        if "configs_dir" not in data:
            raise ValueError(
                f"MASSpec requires one of: 'app', 'manifest', or 'configs_dir'. Got keys: {list(data.keys())}"
            )
        configs_dir = resolve_path_ref(str(data["configs_dir"]), base_dir)
        return cls(
            configs_dir=configs_dir,
            base_scenario=data.get("base_scenario", "baseline"),
        )
