#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared run-agent helpers for mas-runtime headless CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mas.ctl.overlay import merge_overlay
from mas.ctl.overlay.normalize import normalize_overlay
from mas.ctl.overrides import apply_cli_overrides, parse_override
from mas.ctl.validate import validate_data, validate_file, validation_enabled
from mas.runtime.spec.source import load_yaml_file, resolve_manifest_source

_ROOTS_APPLIED_ELSEWHERE = frozenset({"infra", "flavour", "workspace"})


def _apply_agent_overrides(data: dict[str, Any], overrides: tuple[str, ...]) -> dict[str, Any]:
    """Apply standalone ``.agent`` overrides; other session nodes load with those documents."""
    parsed = [(source, parse_override(source)) for source in overrides]
    nested = [source for source, p in parsed if p.path.parent == "mas"]
    if nested:
        raise ValueError(
            f"{nested[0]!r} addresses an agent under a MAS; this session is a standalone agent. "
            "Use .agent.spec…"
        )
    agent = [(source, p) for source, p in parsed if p.path.root not in _ROOTS_APPLIED_ELSEWHERE]
    if not agent:
        return data
    spec = data.get("spec") or {}
    selects_models = any(len(p.path.segments) > 1 and p.path.segments[1].name == "models" for _source, p in agent)
    if selects_models and not spec.get("models"):
        # Same row `mas-ctl compile` writes for an omitted spec.models (model: any inherits).
        data = {**data, "spec": {**spec, "models": [{"id": "main", "model": "any"}]}}
    return apply_cli_overrides(data, tuple(source for source, _p in agent), root="agent")


def build_cli_overlay(
    *,
    tools: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
    memory: str | None = None,
    set_values: tuple[str, ...] = (),
    overrides: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if not any([tools, skills, memory, set_values, overrides]):
        return None
    spec: dict[str, Any] = {}
    if tools:
        spec["tools"] = {"$op": {"add": list(tools)}}
    if skills:
        spec["skills"] = {"$op": {"add": list(skills)}}
    if memory:
        spec["memory"] = memory
    if set_values:
        context: dict[str, str] = {}
        for kv in set_values:
            if "=" not in kv:
                raise ValueError(f"--set must be KEY=VALUE, got {kv!r}")
            key, value = kv.split("=", 1)
            context[key] = value
        spec["context"] = context
    # Canonical mas/v1 Overlay: the CLI patch (tools/skills/memory/context)
    # goes under spec.patch, matching file overlays. A bare {"spec": {...}}
    # is rejected by normalize_overlay/merge_overlay (which require spec.patch).
    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "cli-overlay"},
        "spec": {"target": {"kind": "Agent"}, "patch": spec},
    }


def _agent_from_cli_overlay(cli_ov: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal agent manifest from a CLI-only overlay (no YAML manifest).

    ``build_cli_overlay`` now returns a canonical ``kind: Overlay``; when there
    is no base manifest we merge it into an empty ``kind: Agent`` so downstream
    receives an agent manifest, not an overlay document.
    """
    base = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "agent"},
        "spec": {"context": {}},
    }
    return merge_overlay(base, cli_ov)


def pattern_from_manifest(data: dict) -> str:
    dp = (data.get("spec") or {}).get("design_pattern") or {}
    ptype = dp.get("type", "react") if isinstance(dp, dict) else "react"
    return f"{ptype}@v1"


def load_merged_agent_manifest(
    manifest: Path | str | dict[str, Any] | None,
    *,
    manifest_dir: Path | None = None,
    overlays: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
    memory: str | None = None,
    set_values: tuple[str, ...] = (),
    overrides: tuple[str, ...] = (),
    pattern: str | None = None,
    validate: bool = True,
) -> tuple[dict | None, str]:
    """Load agent YAML (path, ref, or inline dict), apply overlays + CLI patches."""
    if manifest is None:
        plugin = pattern or "react@v1"
        cli_ov = build_cli_overlay(
            tools=tools,
            skills=skills,
            memory=memory,
            set_values=set_values,
            overrides=overrides,
        )
        data = _agent_from_cli_overlay(cli_ov) if cli_ov else None
        if data is not None and overrides:
            data = _apply_agent_overrides(data, overrides)
        return (data, plugin) if data else (None, plugin)

    anchor = manifest_dir or (manifest.parent if isinstance(manifest, Path) else Path.cwd())
    if isinstance(manifest, Path) and validate and validation_enabled():
        validate_file(manifest, kind="agent").raise_if_failed()
    elif isinstance(manifest, str) and validate and validation_enabled():
        from mas.runtime.spec.source import resolve_yaml_path

        validate_file(resolve_yaml_path(manifest.strip(), anchor), kind="agent").raise_if_failed()

    data = resolve_manifest_source(manifest, anchor=anchor)
    if data is None:
        plugin = pattern or "react@v1"
        cli_ov = build_cli_overlay(
            tools=tools,
            skills=skills,
            memory=memory,
            set_values=set_values,
            overrides=overrides,
        )
        data = _agent_from_cli_overlay(cli_ov) if cli_ov else None
        if data is not None and overrides:
            data = _apply_agent_overrides(data, overrides)
        return (data, plugin) if data else (None, plugin)

    for ov in overlays:
        ov_path = Path(ov)
        raw = load_yaml_file(ov_path)
        normalized = normalize_overlay(raw, name=ov_path.stem)
        if validate and validation_enabled():
            validate_data(normalized, source=str(ov_path), kind="overlay").raise_if_failed()
        data = merge_overlay(data, normalized)
    cli_ov = build_cli_overlay(tools=tools, skills=skills, memory=memory, set_values=set_values)
    if cli_ov:
        data = merge_overlay(data, cli_ov)
    if overrides:
        data = _apply_agent_overrides(data, overrides)
        if validate and validation_enabled():
            validate_data(data, source="CLI overrides", kind="agent").raise_if_failed()
    plugin = pattern or pattern_from_manifest(data)
    return data, plugin


def run_headless_agent(instance, *, query: tuple[str, ...], interactive: bool) -> None:
    """Programmatic headless session (tests / embedders)."""
    from mas.ctl.session.controller import ConversationConfig, SessionController, run_session_loop
    from mas.ctl.ui.stdout import StdoutConversationDisplay

    scripted = list(query)
    controller = SessionController(
        instance=instance,
        display=StdoutConversationDisplay(agent_label="Agent"),
        config=ConversationConfig(single_turn=not interactive and len(scripted) <= 1),
    )
    run_session_loop(controller, interactive=interactive, scripted=scripted)
