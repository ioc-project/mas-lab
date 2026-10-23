#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compile a manifest + overlay stack into the resolved in-memory spec.

``mas-ctl compose`` emits EffectiveBind + placement. This module dumps the
author-facing Agent / MAS YAML after overlay merge and runtime default
resolution — the dict ``chat`` / ``run-mas`` actually hold before bootstrap
wiring (delegation tools, skill injection, filesystem tool-ref expansion).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from mas.ctl.manifest.mas_agent_merge import apply_loaded_agent_patch
from mas.ctl.overlay import accumulate_agent_patches, loaded_agent_patches, merge_overlay
from mas.ctl.overlay.normalize import normalize_overlay
from mas.ctl.overrides import apply_root_overrides, overlay_for_session_agent
from mas.ctl.validate import validate_data, validate_file, validation_enabled
from mas.ctl.workspace.config import WorkspaceConfig
from mas.runtime.agent_defaults import (
    agent_defaults,
    default_assembler_id,
    default_pattern_plugin_id,
)
from mas.runtime.spec.model_ref import ANY_MODEL
from mas.runtime.spec.plugin_binding import normalize_plugin_binding, plugin_binding_id
from mas.runtime.spec.source import load_yaml_mapping, resolve_yaml_path

logger = logging.getLogger(__name__)

ResolvedLayout = Literal["tree", "bundle"]

_YAML_SUFFIXES = {".yaml", ".yml"}
_MAS_KINDS = {"mas", "app", "workflow"}
_SKIP_OVERLAY_KINDS = {"flavour", "infra"}


class CompileError(ValueError):
    """Manifest compile failed (kind, overlay target, or layout mismatch)."""


@dataclass
class CompiledManifest:
    """Compiled document; Agent/MAS additionally resolve overlays and defaults."""

    kind: str
    source: Path
    overlay_paths: list[Path]
    agent: dict[str, Any] | None = None
    mas: dict[str, Any] | None = None
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_ids: list[str] = field(default_factory=list)
    agent_relpaths: dict[str, str] = field(default_factory=dict)
    document: dict[str, Any] | None = None


def fill_agent_defaults(doc: dict[str, Any], *, workspace: Any = None) -> dict[str, Any]:
    """Fill omitted agent spec fields with the same defaults the runtime uses."""
    out = copy.deepcopy(doc)
    out.pop("_validation_base_dir", None)
    if str(out.get("kind", "")).lower() != "agent":
        return out
    spec = out.setdefault("spec", {})
    if "context_plugin" in spec:
        raise CompileError("spec.context_plugin was removed; use spec.assembler")
    defaults = agent_defaults(workspace)

    dp = normalize_plugin_binding(spec.get("design_pattern"), field="spec.design_pattern")
    if not plugin_binding_id(dp, field="spec.design_pattern"):
        spec["design_pattern"] = copy.deepcopy(defaults["design_pattern"])
        dp = spec["design_pattern"]
    else:
        spec["design_pattern"] = dp
        if not (dp.get("type") or dp.get("ref")):
            dp["type"] = default_pattern_plugin_id()
    dp_params = dp.get("params")
    if not isinstance(dp_params, dict):
        dp_params = {}
        dp["params"] = dp_params
    from mas.runtime.kernel.config import KernelConfig

    kernel_defaults = KernelConfig()
    if kernel_defaults.max_auto_steps is not None:
        dp_params.setdefault("max_steps", kernel_defaults.max_auto_steps)
    dp_params.setdefault("max_cot_pass", kernel_defaults.max_cot_pass)
    dp_params.setdefault("parallel", kernel_defaults.parallel_tool_calls)

    if not spec.get("models"):
        spec["models"] = copy.deepcopy(defaults["models"])
    else:
        for entry in spec["models"]:
            if isinstance(entry, dict) and not str(entry.get("model") or "").strip():
                entry["model"] = ANY_MODEL

    from mas.library.standard.lib.context.compaction import apply_working_memory_compaction
    from mas.library.standard.lib.context.history_budget import fill_context_manager_defaults

    apply_working_memory_compaction(spec)
    fill_context_manager_defaults(spec)

    asm = normalize_plugin_binding(spec.get("assembler"), field="spec.assembler")
    if not plugin_binding_id(asm, field="spec.assembler"):
        spec["assembler"] = {"type": default_assembler_id()}
        asm = spec["assembler"]
    else:
        spec["assembler"] = asm
        if not (asm.get("type") or asm.get("ref")):
            asm["type"] = default_assembler_id()
    asm_params = asm.get("params")
    if not isinstance(asm_params, dict):
        asm_params = {}
        asm["params"] = asm_params
    asm_params.setdefault("emit_segments", True)
    asm_params.setdefault("always_reassemble", False)

    return out


def fill_mas_defaults(doc: dict[str, Any]) -> dict[str, Any]:
    """Fill omitted MAS ``spec.models`` with ``any`` (local config resolves it)."""
    out = copy.deepcopy(doc)
    out.pop("_validation_base_dir", None)
    spec = out.setdefault("spec", {})
    if not spec.get("models"):
        spec["models"] = [{"id": "main", "model": ANY_MODEL}]
    else:
        for entry in spec["models"]:
            if isinstance(entry, dict) and not str(entry.get("model") or "").strip():
                entry["model"] = ANY_MODEL
    return out


def compile_manifest(
    manifest: Path,
    overlay_paths: list[Path] | None = None,
    *,
    overrides: tuple[str, ...] = (),
    fill_defaults: bool = True,
    validate: bool = True,
    workspace: Any = None,
    schema_kind: str | None = None,
) -> CompiledManifest:
    """Load ``manifest``, apply overlays in order, optionally fill runtime defaults."""
    manifest = Path(manifest)
    overlays = [Path(p) for p in (overlay_paths or [])]
    doc = load_yaml_mapping(manifest)
    kind = str(doc.get("kind") or "").strip()
    kind_l = kind.lower()
    if kind_l not in {"agent"} | _MAS_KINDS:
        from mas.ctl.overrides.apply import apply_manifest_overrides
        from mas.ctl.validate.schemas import declared_kind

        selected_schema = schema_kind or declared_kind(doc)
        if selected_schema is None:
            raise CompileError(f"compile requires a registered manifest schema, got {kind!r}")
        if overlays:
            raise CompileError("file overlays on non-Agent/MAS compile targets are not supported; use --override")
        if validate and validation_enabled():
            validate_data(doc, source=str(manifest), kind=selected_schema).raise_if_failed()
        result = apply_manifest_overrides(
            doc,
            overrides,
            validate=validate and validation_enabled(),
            schema_kind=selected_schema,
        )
        return CompiledManifest(
            kind=kind or selected_schema,
            source=manifest,
            overlay_paths=overlays,
            document=result,
        )

    if validate and validation_enabled():
        validate_file(manifest, kind="agent" if kind_l == "agent" else "mas").raise_if_failed()

    classified = _classify_overlays(overlays, validate=validate)
    ws = workspace if workspace is not None else WorkspaceConfig.load(manifest.parent)

    if kind_l == "agent":
        if classified.mas:
            names = ", ".join(p.name for p, _ in classified.mas)
            raise CompileError(f"cannot apply MAS overlay(s) to an Agent manifest: {names}")
        agent = doc
        for ov_path, overlay in classified.agent:
            _check_agent_overlay_name(overlay, ov_path, agent)
            agent = merge_overlay(agent, overlay)
        if overrides:
            agent, _ = _apply_compile_overrides(agent, overrides, root="agent", validate=validate)
        if fill_defaults:
            agent = fill_agent_defaults(agent, workspace=ws)
        if validate and overrides and validation_enabled():
            validate_data(agent, source="CLI overrides", kind="agent").raise_if_failed()
        return CompiledManifest(
            kind="Agent",
            source=manifest,
            overlay_paths=overlays,
            agent=_sanitize_doc(agent),
        )

    mas = doc
    agent_patches: dict[str, dict] = {}
    for _ov_path, overlay in classified.mas:
        mas = merge_overlay(mas, overlay)
        agent_patches = accumulate_agent_patches(agent_patches, loaded_agent_patches(overlay, mas))
    if overrides:
        mas, cli_agent_patches = _apply_compile_overrides(mas, overrides, root="mas", validate=validate)
        agent_patches = accumulate_agent_patches(agent_patches, cli_agent_patches)
    if fill_defaults:
        mas = fill_mas_defaults(mas)
    if validate and overrides and validation_enabled():
        validate_data(mas, source="CLI overrides", kind="mas").raise_if_failed()

    agents, agent_ids, relpaths = _load_mas_agents(mas, mas_dir=manifest.parent)
    for aid, adoc in list(agents.items()):
        agents[aid] = apply_loaded_agent_patch(adoc, agent_id=aid, agent_patches=agent_patches, mas_config=mas)
    for ov_path, overlay in classified.agent:
        target_name = _overlay_target_name(overlay)
        if target_name:
            targets = [aid for aid, adoc in agents.items() if _agent_matches(adoc, aid, target_name)]
            if not targets:
                raise CompileError(
                    f"overlay {ov_path.name} targets agent {target_name!r}, not in compiled MAS agents {agent_ids!r}"
                )
        else:
            targets = list(agent_ids)
        for aid in targets:
            agents[aid] = merge_overlay(agents[aid], overlay)

    if fill_defaults:
        agents = {aid: fill_agent_defaults(adoc, workspace=ws) for aid, adoc in agents.items()}
    agents = {aid: _sanitize_doc(adoc) for aid, adoc in agents.items()}

    return CompiledManifest(
        kind="MAS",
        source=manifest,
        overlay_paths=overlays,
        mas=_sanitize_doc(mas),
        agents=agents,
        agent_ids=agent_ids,
        agent_relpaths=relpaths,
    )


def _apply_compile_overrides(
    document: dict[str, Any], overrides: tuple[str, ...], *, root: str, validate: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply session-tree assignments in CLI order."""
    from mas.ctl.overrides.parser import parse_override

    result = document
    extra: dict[str, Any] = {}
    for source in overrides:
        path = parse_override(source).path
        if path.root == "manifest":
            raise CompileError(
                f"override root 'manifest' is not a session node; this file is {root!r} "
                f"— use .{root}.…"
            )
        if root == "mas" and path.root == "agent" and path.parent == "mas":
            overlay = overlay_for_session_agent(source)
            result = merge_overlay(result, overlay)
            extra = accumulate_agent_patches(extra, loaded_agent_patches(overlay, result))
            continue
        if root == "mas" and path.root == "agent":
            raise CompileError(
                f"{source!r} addresses a standalone agent; this document is a MAS. "
                "Use .mas.agent[id].spec…"
            )
        if root == "agent" and path.parent == "mas":
            raise CompileError(
                f"{source!r} addresses an agent under a MAS; this document is a standalone agent. "
                "Use .agent.spec…"
            )
        if path.root == root and path.parent is None:
            result = apply_root_overrides(result, (source,), root=root)
        else:
            raise CompileError(
                f"compile override {path.text} is not valid for this document; use .{root}.…"
            )
    return result, extra


def resolve_layout(output: Path | None, layout: str) -> ResolvedLayout:
    """Pick tree vs bundle from ``--layout`` and the output path shape."""
    layout_l = layout.lower()
    if layout_l not in {"auto", "tree", "bundle"}:
        raise CompileError(f"unknown layout {layout!r}")
    if layout_l == "tree":
        if output is None:
            raise CompileError("--layout tree requires --output <directory>")
        if output.suffix.lower() in _YAML_SUFFIXES:
            raise CompileError("--layout tree writes a directory, not a YAML file")
        return "tree"
    if layout_l == "bundle":
        return "bundle"
    if output is None or output.suffix.lower() in _YAML_SUFFIXES:
        return "bundle"
    return "tree"


def compiled_documents(compiled: CompiledManifest, layout: ResolvedLayout) -> dict[str, dict[str, Any]]:
    """Map relative output paths to YAML documents for the chosen layout."""
    if compiled.document is not None:
        return {compiled.source.name: copy.deepcopy(compiled.document)}
    if compiled.kind == "Agent":
        if compiled.agent is None:
            raise CompileError("compiled Agent is missing agent document")
        name = compiled.source.name if compiled.source.suffix.lower() in _YAML_SUFFIXES else "agent.yaml"
        return {name: compiled.agent}
    if compiled.mas is None:
        raise CompileError("compiled MAS is missing mas document")
    if layout == "bundle":
        return {"mas.yaml": as_bundle_document(compiled)}
    docs: dict[str, dict[str, Any]] = {"mas.yaml": as_tree_mas(compiled)}
    for aid in compiled.agent_ids:
        rel = compiled.agent_relpaths.get(aid) or f"agents/{aid}.yaml"
        docs[rel] = compiled.agents[aid]
    return docs


def as_bundle_document(compiled: CompiledManifest) -> dict[str, Any]:
    """Single YAML document: Agent as-is, or MAS with agents inlined."""
    if compiled.kind == "Agent":
        if compiled.agent is None:
            raise CompileError("compiled Agent is missing agent document")
        return copy.deepcopy(compiled.agent)
    if compiled.mas is None:
        raise CompileError("compiled MAS is missing mas document")
    mas = copy.deepcopy(compiled.mas)
    spec = mas.setdefault("spec", {})
    agency = spec.setdefault("agency", {})
    inlined: list[dict[str, Any]] = []
    for aid in compiled.agent_ids:
        agent = copy.deepcopy(compiled.agents[aid])
        meta = agent.setdefault("metadata", {})
        if not meta.get("name"):
            meta["name"] = aid
        inlined.append(agent)
    agency["agents"] = inlined
    return mas


def as_tree_mas(compiled: CompiledManifest) -> dict[str, Any]:
    """MAS document with agency entries reduced to ``{id, ref}`` for a folder dump."""
    if compiled.mas is None:
        raise CompileError("compiled MAS is missing mas document")
    mas = copy.deepcopy(compiled.mas)
    spec = mas.setdefault("spec", {})
    agency = spec.setdefault("agency", {})
    agency["agents"] = [
        {"id": aid, "ref": compiled.agent_relpaths.get(aid) or f"agents/{aid}.yaml"} for aid in compiled.agent_ids
    ]
    return mas


def dump_yaml(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def render_header(compiled: CompiledManifest) -> str:
    lines = [
        "# Generated by mas-ctl compile — overlays applied, runtime defaults resolved.",
        f"# Source: {compiled.source}",
    ]
    if compiled.overlay_paths:
        overlay_list = ", ".join(str(p) for p in compiled.overlay_paths)
        lines.append(f"# Overlays: {overlay_list}")
    return "\n".join(lines) + "\n"


def write_compiled(
    compiled: CompiledManifest,
    *,
    output: Path | None,
    layout: ResolvedLayout,
    header: bool = True,
) -> list[Path]:
    """Write compiled YAML. ``output is None`` is a caller-handled stdout case."""
    if output is None:
        raise CompileError("write_compiled requires an output path")
    docs = compiled_documents(compiled, layout)
    prefix = render_header(compiled) if header else ""
    written: list[Path] = []

    if layout == "bundle":
        if output.suffix.lower() in _YAML_SUFFIXES:
            path = output
        else:
            output.mkdir(parents=True, exist_ok=True)
            path = output / next(iter(docs))
        path.parent.mkdir(parents=True, exist_ok=True)
        body = dump_yaml(next(iter(docs.values())))
        path.write_text(prefix + body, encoding="utf-8")
        written.append(path)
        return written

    output.mkdir(parents=True, exist_ok=True)
    for rel, doc in docs.items():
        path = output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prefix + dump_yaml(doc), encoding="utf-8")
        written.append(path)
    return written


@dataclass
class _ClassifiedOverlays:
    mas: list[tuple[Path, dict[str, Any]]]
    agent: list[tuple[Path, dict[str, Any]]]


def _classify_overlays(
    overlay_paths: list[Path],
    *,
    validate: bool,
) -> _ClassifiedOverlays:
    classified = _ClassifiedOverlays(mas=[], agent=[])
    for ov_path in overlay_paths:
        raw = load_yaml_mapping(ov_path)
        overlay = normalize_overlay(raw, name=ov_path.stem)
        if validate and validation_enabled():
            validate_data(overlay, source=str(ov_path), kind="overlay").raise_if_failed()
        target_kind = str((overlay.get("spec") or {}).get("target", {}).get("kind") or "").lower()
        if target_kind in _MAS_KINDS:
            classified.mas.append((ov_path, overlay))
        elif target_kind == "agent":
            classified.agent.append((ov_path, overlay))
        elif target_kind in _SKIP_OVERLAY_KINDS:
            logger.warning(
                "skipping %s overlay %s (compile emits Agent/MAS specs only)",
                target_kind,
                ov_path,
            )
        else:
            raise CompileError(f"overlay {ov_path} has unsupported target.kind {target_kind!r}; expected Agent or MAS")
    return classified


def _overlay_target_name(overlay: dict[str, Any]) -> str | None:
    name = (overlay.get("spec") or {}).get("target", {}).get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _check_agent_overlay_name(overlay: dict[str, Any], ov_path: Path, agent: dict[str, Any]) -> None:
    target_name = _overlay_target_name(overlay)
    if not target_name:
        return
    agent_id = _agent_id(agent)
    if not _agent_matches(agent, agent_id, target_name):
        raise CompileError(f"overlay {ov_path.name} targets agent {target_name!r}, not {agent_id!r}")


def _agent_id(agent: dict[str, Any]) -> str:
    meta = agent.get("metadata") or {}
    return str(meta.get("name") or agent.get("id") or "agent")


def _agent_matches(agent: dict[str, Any], agency_id: str, target_name: str) -> bool:
    return target_name in {agency_id, _agent_id(agent)}


def _is_inline_agent(entry: dict[str, Any]) -> bool:
    return str(entry.get("kind", "")).lower() == "agent" and isinstance(entry.get("spec"), dict)


def _looks_like_scheme_ref(ref: str) -> bool:
    scheme, sep, rest = ref.partition(":")
    return bool(sep and scheme and "/" not in scheme and "\\" not in scheme and rest)


def _agent_relpath(entry: dict[str, Any], agent_id: str) -> str:
    ref = entry.get("ref")
    if isinstance(ref, str) and ref.strip():
        raw = ref.strip()
        path = Path(raw)
        if not path.is_absolute() and not _looks_like_scheme_ref(raw):
            return raw
    return f"agents/{agent_id}.yaml"


def _agency_entries(mas: dict[str, Any]) -> list[dict[str, Any]]:
    spec = mas.get("spec") or {}
    agency = spec.get("agency") or {}
    entries = list(agency.get("agents") or spec.get("agents") or [])
    return [e for e in entries if isinstance(e, dict)]


def _entry_agent_id(entry: dict[str, Any], fallback: str) -> str:
    if _is_inline_agent(entry):
        return str(entry.get("metadata", {}).get("name") or entry.get("id") or fallback)
    return str(entry.get("id") or entry.get("name") or fallback)


def _load_mas_agents(
    mas: dict[str, Any],
    *,
    mas_dir: Path,
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
    agents: dict[str, dict[str, Any]] = {}
    agent_ids: list[str] = []
    relpaths: dict[str, str] = {}
    for index, entry in enumerate(_agency_entries(mas)):
        aid = _entry_agent_id(entry, f"agent-{index}")
        if aid in agents:
            continue
        if _is_inline_agent(entry):
            doc = copy.deepcopy(entry)
        else:
            ref = entry.get("ref")
            if not isinstance(ref, str) or not ref.strip():
                raise CompileError(f"agency agent {aid!r} is missing ref and is not an inline Agent")
            path = resolve_yaml_path(ref.strip(), mas_dir)
            doc = load_yaml_mapping(path)
        agents[aid] = doc
        agent_ids.append(aid)
        relpaths[aid] = _agent_relpath(entry, aid)
    return agents, agent_ids, relpaths


def _sanitize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    out.pop("_validation_base_dir", None)
    return out
