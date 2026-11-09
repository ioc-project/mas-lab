#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

_MISSING = object()


def _apply_overlay_workflow(overlay_spec: dict, config: dict) -> None:
    """Apply ``spec.patch.workflow`` before per-agent patches so ``$entry`` can resolve."""
    if "workflow" not in overlay_spec:
        return
    config["workflow"] = overlay_spec["workflow"]
    new_entry = overlay_spec["workflow"].get("entry") if isinstance(overlay_spec["workflow"], dict) else None
    if new_entry:
        config.setdefault("mas", {})["entry_agent"] = new_entry


def _resolved_overlay_agents(overlay_spec: dict, config: dict) -> dict:
    """Resolve ``patch.agents.$entry`` against the merged workflow entry."""
    from mas.ctl.overlay.merge import _resolve_mas_agent_patches

    raw = overlay_spec.get("agents")
    if not isinstance(raw, dict) or raw.get("$op") is not None:
        return raw if isinstance(raw, dict) else {}
    workflow = dict(config.get("workflow") or {})
    if not workflow.get("entry"):
        workflow["entry"] = str((config.get("mas") or {}).get("entry_agent") or "").strip()
    spec = {"workflow": workflow, "agency": {"agents": config.get("agents") or []}}
    return _resolve_mas_agent_patches(raw, spec)


def _apply_overlay_agent_collection_ops(overlay_spec: dict, config: dict, mas_yaml: Path) -> None:
    """Apply the canonical ``patch.agents.$op`` operations to loaded agent configs."""
    from mas.ctl.overlay.merge import _ops_dict

    ops = _ops_dict(overlay_spec.get("agents"))
    if ops is None:
        return

    agents = list(config.get("agents") or [])
    if ops.get("clear") is True:
        agents = []
    has_replace = "replace" in ops
    replace_entries = list(ops.get("replace") or [])
    add_entries = list(ops.get("add") or [])
    if has_replace:
        agents = []

    if has_replace or add_entries:
        from mas.lab.manifest.load import load_agent_runtime_entry
        from mas.runtime.spec.source import resolve_yaml_path

        existing_ids = {str(agent.get("id") or agent.get("name")) for agent in agents}

        def _append_entries(entries: list, *, skip_existing: bool) -> None:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                agent_id = entry.get("id") or entry.get("name")
                agent_key = str(agent_id)
                if skip_existing and agent_key in existing_ids:
                    continue
                reference = entry.get("ref")
                if reference:
                    path = resolve_yaml_path(str(reference), mas_yaml.parent)
                    try:
                        loaded = load_agent_runtime_entry(path, agent_id=agent_id)
                    except Exception as exc:
                        logger.warning("Overlay agent %r: failed to load ref %s: %s", agent_id, reference, exc)
                        loaded = entry
                else:
                    loaded = entry
                agents.append(loaded)
                existing_ids.add(agent_key)

        _append_entries(replace_entries, skip_existing=False)

    if "remove" in ops:
        remove_ids = {str(agent_id) for agent_id in ops.get("remove") or []}
        agents = [agent for agent in agents if str(agent.get("id") or agent.get("name")) not in remove_ids]

    if add_entries:
        existing_ids.clear()
        existing_ids.update(str(agent.get("id") or agent.get("name")) for agent in agents)
        _append_entries(add_entries, skip_existing=True)

    config["agents"] = agents


def _apply_overlay_plugin_list(
    overlay: Optional[dict],
    overlay_spec: dict,
    config: dict,
    field: str,
) -> bool:
    """Union an overlay plugin list onto every agent's *field*.

    Same absent-vs-empty distinction as :func:`_apply_overlay_governance`.
    A raw list concatenates (with plugin-id dedup). ``{"$op": ...}`` uses
    overlay merge semantics so ``$op.add`` / ``$op.clear`` match Agent YAML.
    """
    from mas.ctl.overlay.merge import _ops_dict, merge_agent_overlay

    incoming = _MISSING
    if overlay:
        incoming = overlay.get("spec", {}).get(field, _MISSING)
    if incoming is _MISSING:
        incoming = overlay_spec.get(field, _MISSING)
    if incoming is _MISSING or not config.get("agents"):
        return False

    ops = _ops_dict(incoming)
    if ops is None:
        patch_value = list(incoming or [])
        if not patch_value:
            for agent_cfg in config["agents"]:
                agent_cfg[field] = []
            return True
        patch_value = {"$op": {"add": patch_value}}
    else:
        patch_value = incoming

    overlay_doc = {"spec": {"patch": {field: patch_value}}}
    for agent_cfg in config["agents"]:
        stub = {"spec": {field: list(agent_cfg.get(field) or [])}}
        merged = merge_agent_overlay(stub, overlay_doc)
        agent_cfg[field] = list((merged.get("spec") or {}).get(field) or [])
    return True


def _stacked_entry_and_delegates(config: dict) -> tuple[str, list[str]]:
    """``$entry`` / ``$delegates`` for a stacked lab config.

    Entry is ``workflow.entry``, then ``mas.entry_agent``. Omitted, ``$entry``
    matches nobody. Delegates are that entry node's ``delegates_to`` list;
    omitted or empty, ``$delegates`` matches nobody.
    """
    workflow = config.get("workflow") if isinstance(config.get("workflow"), dict) else {}
    entry = str((workflow or {}).get("entry") or ((config.get("mas") or {}).get("entry_agent") or ""))
    for node in (workflow or {}).get("nodes") or []:
        if isinstance(node, dict) and str(node.get("id") or "") == entry:
            raw = node.get("delegates_to") or []
            return entry, [str(item) for item in raw] if isinstance(raw, list) else []
    return entry, []


def _lab_flavour(config: dict) -> dict:
    flavour = config.get("_lab_flavour_spec")
    if not isinstance(flavour, dict):
        flavour = {"_controls": []}
        config["_lab_flavour_spec"] = flavour
    flavour.setdefault("_controls", [])
    return flavour


def _overlay_targets_control(overlay: Optional[dict]) -> bool:
    if not isinstance(overlay, dict):
        return False
    if overlay.get("kind") == "Control":
        return True
    target = (overlay.get("spec") or {}).get("target") or {}
    return overlay.get("kind") == "Overlay" and str(target.get("kind") or "").strip().lower() == "control"


def _rewrite_agent_control_lists(config: dict, flavour: dict) -> None:
    from mas.ctl.session.flavour_control.documents import resolve_controls
    from mas.ctl.session.flavour_control.plugins import control_lists_for_agent
    from mas.ctl.session.flavour_control.spec import (
        GOVERNANCE,
        OBSERVABILITY,
        empty_list_silences_warning,
    )
    from mas.ctl.session.notices import warn_once

    rows = resolve_controls(flavour)
    entry, delegates = _stacked_entry_and_delegates(config)
    for agent_cfg in config.get("agents") or []:
        aid = str(agent_cfg.get("id") or agent_cfg.get("name") or "")
        lists = control_lists_for_agent(
            rows,
            agent_id=aid,
            entry_id=entry,
            delegate_ids=delegates,
        )
        for key in (OBSERVABILITY, GOVERNANCE):
            if key in lists:
                if agent_cfg.get(key) and not lists[key]:
                    warn_once(empty_list_silences_warning(aid, key))
                agent_cfg[key] = lists[key]


def _apply_control_pipeline(overlay: Optional[dict], config: dict, overlay_path: Path) -> bool:
    """Copy matching Control lists onto stacked agents, replacing leftover when a Control names the list.

    Lab config agents are the bench kernel input. Session attach does not copy
    these lists onto an Agent document.
    Overlay patches that target ``kind: Control`` apply to the stacked Controls.
    """
    if not _overlay_targets_control(overlay):
        return False
    from mas.ctl.session.flavour_control.attach import apply_control_overlays
    from mas.ctl.session.flavour_control.documents import row_from_loaded, validate_control_document

    flavour = _lab_flavour(config)
    if overlay.get("kind") == "Control":
        overlay = validate_control_document(overlay, overlay_path)
        flavour["_controls"].append(row_from_loaded(overlay_path, overlay, agents=None))
    else:
        apply_control_overlays(flavour, [overlay_path])
    _rewrite_agent_control_lists(config, flavour)
    return True


def _apply_overlay_governance(overlay: Optional[dict], overlay_spec: dict, config: dict) -> bool:
    """Inject an overlay's governance plugin list onto each agent's own
    "governance" list, mirroring the spec.plugins merge elsewhere in this
    module. Consumed by parse_gov_spec/build_kernel_config -> KernelConfig.policy_engine.

    Distinguishes the governance key being absent (nothing to do — a prior
    overlay's contribution in the same stack, if any, is left alone) from it
    being explicitly present, even as an empty list: an explicit ``[]`` is
    the overlay author asking to clear whatever governance a previous overlay
    in this stack already contributed, not a no-op — concatenating an empty
    list onto the existing accumulation silently ignored that intent.

    Returns True when a governance key was present and applied (for the
    caller's own debug logging), False when there was nothing to do.
    """
    return _apply_overlay_plugin_list(overlay, overlay_spec, config, "governance")


def discover_scenario_stems(scenarios_dir: Path) -> List[str]:
    """Return sorted scenario stems from *scenarios_dir*.

    Supports:
    - Flat overlay files: ``<dir>/<id>.yaml``
    - Subdirectory apps:  ``<dir>/<id>/mas.yaml``

    If multiple formats exist for the same stem, they are deduplicated.
    """
    stems: set[str] = set()
    if not scenarios_dir.exists():
        return []
    for p in scenarios_dir.glob("*.yaml"):
        if p.stem != "mas":
            stems.add(p.stem)
    for p in scenarios_dir.iterdir():
        if p.is_dir() and (p / "mas.yaml").exists():
            stems.add(p.name)
    return sorted(stems)


def load_scenario_config(
    scenarios_dir: Path,
    scenario_id: str,
    mas_yaml: Optional[Path] = None,
    *,
    infra_refs: list[str] | None = None,
    workspace_root: Optional[Path] = None,
) -> tuple[dict, Path]:
    """Load a scenario config from *scenarios_dir*, overlay-first.

    Resolution order:
    1. ``<scenarios_dir>/<scenario_id>.yaml``  — MAS overlay applied on top
       of the base ``mas.yaml``.  By default ``mas.yaml`` is expected at
       ``<scenarios_dir>/../mas.yaml``; pass *mas_yaml* to override this when
       the experiment keeps overlays in a subdirectory that is not adjacent to
       the shared ``mas.yaml`` (e.g. ``experiments/02-injection/overlays/``).

    Overlay spec fields propagated into the merged config:

    * ``spec.patch.capabilities``  → ``config["capabilities"]``  (merge/update)
    * ``spec.patch.telemetry``     → ``config["mas"]["telemetry"]`` (merge/update)
    * ``spec.patch.params``        → ``config["params"]`` (replace)
      Key/value pairs opaque to the runner; tools read them as
      ``ctx.runtime_params``.
    * ``spec.patch.skills_exclude``→ ``config["skills_exclude"]`` (replace list)

    Returns
    -------
    (config_dict, base_path)
        *config_dict* is the fully merged MAS config ready for ``MasRuntime``.
        *base_path* is the anchor for relative-path resolution (the ``mas.yaml``
        parent for overlays).

    Raises
    ------
    FileNotFoundError
        When no ``.yaml`` overlay is found.
    """
    # 1. Prefer overlay file — flat or subdirectory layout
    overlay_path = scenarios_dir / f"{scenario_id}.yaml"
    # apps/<id>/mas.yaml  (subdirectory layout used by apps/)
    if not overlay_path.exists():
        _subdir_path = scenarios_dir / scenario_id / "mas.yaml"
        if _subdir_path.exists():
            overlay_path = _subdir_path

    if overlay_path.exists():
        from mas.lab.manifest.load import load_mas_config
        from mas.runtime.spec.source import load_yaml_mapping

        overlay = load_yaml_mapping(overlay_path)

        # Distinguish standalone MAS manifests from scenario overlays.
        # Files with ``kind: MAS`` (or ``kind: Workflow``) are full manifests
        # that define their own agents and topology — load them directly
        # instead of patching a base mas.yaml.
        _overlay_kind = (overlay or {}).get("kind", "")
        if _overlay_kind in ("MAS", "Workflow"):
            mas_manifest = load_mas_config(
                overlay_path,
                validate=False,
                infra_refs=infra_refs,
                workspace_root=workspace_root,
            )
            config = dict(mas_manifest._raw)
            # Store raw overlay for cache key coverage even on full-manifest overlays.
            config["_overlay_hash_input"] = [overlay]
            return config, overlay_path

        # Resolve mas.yaml: explicit argument > sibling of configs_dir
        _mas_yaml: Path = mas_yaml if mas_yaml is not None else scenarios_dir.parent / "mas.yaml"
        if not _mas_yaml.exists():
            raise FileNotFoundError(f"mas.yaml not found for overlay {overlay_path}: expected at {_mas_yaml}")
        mas_yaml = _mas_yaml
        # validate=False: allow single-agent Workflow manifests in lab overlay contexts.
        mas_manifest = load_mas_config(
            mas_yaml,
            validate=False,
            infra_refs=infra_refs,
            workspace_root=workspace_root,
        )  # type: ignore[arg-type]
        config = dict(mas_manifest._raw)

        # Inject overlay plugins into each agent in the MAS config so that
        # MasRuntime._build_agents can load them at runtime.
        # Supports both:
        #   spec.plugins         (direct overlay format)
        #   spec.patch.plugins   (kind:Patch format used by locomo mem0 overlays)
        overlay_spec = overlay.get("spec", {}).get("patch", {}) if overlay else {}
        if overlay and overlay.get("kind") != "Control":
            _overlay_agent_plugins = overlay.get("spec", {}).get("plugins") or overlay_spec.get("plugins")
            if _overlay_agent_plugins and config.get("agents"):
                for _agent_cfg in config["agents"]:
                    _existing = list(_agent_cfg.get("plugins") or [])
                    _agent_cfg["plugins"] = _existing + list(_overlay_agent_plugins)

        if "capabilities" in overlay_spec:
            config.setdefault("capabilities", {}).update(overlay_spec["capabilities"])
        if "telemetry" in overlay_spec:
            config.setdefault("mas", {}).setdefault("telemetry", {}).update(overlay_spec["telemetry"])
        # Opaque params; tools read them as ctx.runtime_params.
        if "params" in overlay_spec:
            config["params"] = overlay_spec["params"]
        # Tool / skill filtering declared at overlay level
        if "skills_exclude" in overlay_spec:
            config["skills_exclude"] = overlay_spec["skills_exclude"]
        # skills_include: global skills to ADD to every agent (array-append semantics
        # that RFC 7396 Merge Patch cannot express natively).
        if "skills_include" in overlay_spec:
            config["skills_include"] = overlay_spec["skills_include"]
        _apply_overlay_workflow(overlay_spec, config)
        # Per-agent overrides: spec.patch.agents.<id>.context / design_pattern
        if "agents" in overlay_spec:
            overlay_agents: dict = _resolved_overlay_agents(overlay_spec, config)
            agents_list: list = config.get("agents", [])
            for agent_cfg in agents_list:
                agent_id = agent_cfg.get("id", "")
                per_agent = overlay_agents.get(agent_id, {})
                if not per_agent:
                    continue
                ctx_override = per_agent.get("context") or {}
                if isinstance(ctx_override, dict) and ctx_override:
                    merged = dict(agent_cfg.get("context") or {})
                    merged.update(ctx_override)
                    agent_cfg["context"] = merged
                    logger.info("[overlay] agent '%s': context overridden (scenario=%s)", agent_id, scenario_id)
                for key in ("design_pattern",):
                    if key in per_agent:
                        agent_cfg[key] = per_agent[key]
                        logger.info("[overlay] agent '%s': %s overridden (scenario=%s)", agent_id, key, scenario_id)
                # Per-agent tools: merge new tool names into spec_tools.
                if "tools" in per_agent:
                    existing_tools = list(agent_cfg.get("spec_tools") or [])
                    for t in per_agent["tools"]:
                        if t not in existing_tools:
                            existing_tools.append(t)
                    agent_cfg["spec_tools"] = existing_tools
                    logger.info(
                        "[overlay] agent '%s': spec_tools = %s (scenario=%s)", agent_id, existing_tools, scenario_id
                    )
                # Per-agent skills (add): merge new skill names.
                if "skills" in per_agent:
                    existing_skills = list(agent_cfg.get("skills") or [])
                    existing_set = set(existing_skills)
                    for sk in per_agent["skills"] or []:
                        if sk not in existing_set:
                            existing_skills.append(sk)
                            existing_set.add(sk)
                    agent_cfg["skills"] = existing_skills
                    logger.info(
                        "[overlay] agent '%s': skills = %s (scenario=%s)", agent_id, existing_skills, scenario_id
                    )
                # Per-agent llm override: deep-merge so individual keys can change.
                if "llm" in per_agent:
                    if isinstance(per_agent["llm"], dict):
                        base_llm = agent_cfg.get("llm") or {}
                        base_llm.update(per_agent["llm"])
                        agent_cfg["llm"] = base_llm
                        # Also keep flat llm_model in sync for runtimes that read it
                        if "model" in per_agent["llm"]:
                            agent_cfg["llm_model"] = per_agent["llm"]["model"]
                    else:
                        agent_cfg["llm"] = per_agent["llm"]
                    logger.info("[overlay] agent '%s': llm overridden (scenario=%s)", agent_id, scenario_id)
                if "memory_seed" in per_agent:
                    existing_seed = list(agent_cfg.get("memory_seed") or [])
                    existing_seed.extend(per_agent["memory_seed"] or [])
                    agent_cfg["memory_seed"] = existing_seed
                    logger.info(
                        "[overlay] agent '%s': memory_seed += %d items (scenario=%s)",
                        agent_id,
                        len(per_agent["memory_seed"] or []),
                        scenario_id,
                    )
            unresolved = set(overlay_agents) - {a.get("id") for a in agents_list}
            if unresolved:
                logger.warning(
                    "[overlay] spec.agents keys not matched in mas.yaml agents: %s",
                    ", ".join(sorted(unresolved)),
                )

        _apply_overlay_agent_collection_ops(overlay_spec, config, mas_yaml)  # type: ignore[arg-type]

        # Global design-pattern override: spec.patch.design_pattern → set
        # pattern_framework (and optional pattern_params) on ALL agents.
        # Ignores spec.target — applies to all agents in the MAS.
        dp_spec = overlay_spec.get("design_pattern")
        if dp_spec and config.get("agents"):
            dp_type = dp_spec.get("type", "react") if isinstance(dp_spec, dict) else str(dp_spec)
            dp_params = (dp_spec.get("config") or dp_spec.get("params") or {}) if isinstance(dp_spec, dict) else {}
            for _agent_cfg in config["agents"]:
                _agent_cfg["pattern_framework"] = dp_type
                if dp_params:
                    _agent_cfg.setdefault("pattern_params", {}).update(dp_params)
            logger.debug("[overlay] design_pattern set to '%s' for all agents (scenario=%s)", dp_type, scenario_id)

        if _apply_control_pipeline(overlay, config, overlay_path):
            logger.debug("[overlay] Control lists copied onto stacked agents (scenario=%s)", scenario_id)
        elif _apply_overlay_governance(overlay, overlay_spec, config):
            logger.debug("[overlay] governance policies injected (scenario=%s)", scenario_id)
        if overlay and overlay.get("kind") != "Control":
            if _apply_overlay_plugin_list(overlay, overlay_spec, config, "observability"):
                logger.debug("[overlay] observability sinks injected (scenario=%s)", scenario_id)

        # Embed the full raw overlay in the config so _compute_run_hash captures
        # every overlay dimension automatically — including any fields not yet
        # handled by explicit overlay application above.  The hash function's
        # _materialize_config deep-copies the config dict, so _overlay_hash_input
        # flows into the SHA-256 payload without any extra plumbing.
        config["_overlay_hash_input"] = [overlay]
        config.pop("_lab_flavour_spec", None)

        return config, mas_yaml

    raise FileNotFoundError(f"Scenario '{scenario_id}' not found in {scenarios_dir} (checked {scenario_id}.yaml)")


def load_stacked_config(
    mas_yaml: Path,
    overlay_ids: "List[Union[str, dict]]",
    overlays_dir: Optional[Path] = None,
    *,
    base_dir: Optional[Path] = None,
    infra_refs: list[str] | None = None,
    workspace_root: Optional[Path] = None,
) -> tuple[dict, Path]:
    """Stack multiple overlays in order on top of the base ``mas.yaml``.

    Kustomize-style: overlay files are read from *overlays_dir* (defaulting to
    ``<mas_yaml_parent>/overlays/``) and applied sequentially.  Each overlay
    merges its ``spec`` fields into the accumulated config using the same rules
    as :func:`load_scenario_config`.

    Parameters
    ----------
    mas_yaml:
        Absolute path to the ``mas.yaml`` manifest.
    overlay_ids:
        Ordered list of overlay entries.  Each entry is either a plain string
        (file stem resolved inside *overlays_dir*) or a ``{ref: path}`` dict
        whose path is resolved relative to *base_dir*.
    overlays_dir:
        Directory containing overlay YAML files.  When *None*, defaults to
        ``mas_yaml.parent / "overlays"``.  Pass an explicit path when the
        experiment keeps its overlays separate from the app (e.g. a lab
        referencing an app via ``mas.app``).
    base_dir:
        Root directory for resolving ``{ref: path}`` overlay entries.  When
        *None*, defaults to ``mas_yaml.parent``.

    Returns
    -------
    (config_dict, mas_yaml)
        *config_dict* is the fully merged MAS config ready for ``MasRuntime``.
        *mas_yaml* is returned as the base path (for relative-path resolution).

    Raises
    ------
    FileNotFoundError
        When ``mas_yaml`` or any overlay file is missing.
    """
    if not mas_yaml.exists():
        raise FileNotFoundError(f"mas.yaml not found: {mas_yaml}")

    from mas.lab.manifest.load import load_mas_config
    from mas.runtime.spec.source import load_yaml_mapping

    mas_manifest = load_mas_config(
        mas_yaml,
        validate=False,
        infra_refs=infra_refs,
        workspace_root=workspace_root,
    )
    config = dict(mas_manifest._raw)

    _overlays_dir = overlays_dir if overlays_dir is not None else mas_yaml.parent / "overlays"
    _base_dir = base_dir if base_dir is not None else mas_yaml.parent
    _applied_overlays: list = []
    for overlay_entry in overlay_ids:
        if isinstance(overlay_entry, dict) and "ref" in overlay_entry:
            overlay_path = (_base_dir / overlay_entry["ref"]).resolve()
        else:
            overlay_id = str(overlay_entry)
            overlay_path = _overlays_dir / f"{overlay_id}.yaml"
        if not overlay_path.exists():
            raise FileNotFoundError(f"Overlay '{overlay_entry}' not found: {overlay_path}")
        overlay = load_yaml_mapping(overlay_path)
        _overlay_kind = (overlay or {}).get("kind", "")
        if _overlay_kind in ("MAS", "Workflow"):
            mas_manifest = load_mas_config(
                overlay_path,
                validate=False,
                infra_refs=infra_refs,
                workspace_root=workspace_root,
            )
            config = dict(mas_manifest._raw)
            _applied_overlays = [overlay]
            config["_overlay_hash_input"] = _applied_overlays
            return config, overlay_path

        overlay_spec = overlay.get("spec", {}).get("patch", {})
        if "capabilities" in overlay_spec:
            config.setdefault("capabilities", {}).update(overlay_spec["capabilities"])
        if "telemetry" in overlay_spec:
            config.setdefault("mas", {}).setdefault("telemetry", {}).update(overlay_spec["telemetry"])
        if "params" in overlay_spec:
            config["params"] = overlay_spec["params"]
        if "skills_exclude" in overlay_spec:
            config["skills_exclude"] = overlay_spec["skills_exclude"]
        if "skills_include" in overlay_spec:
            config["skills_include"] = overlay_spec["skills_include"]
        _apply_overlay_workflow(overlay_spec, config)
        # Per-agent overrides: spec.patch.agents.<id>.context / design_pattern
        if "agents" in overlay_spec:
            overlay_agents: dict = _resolved_overlay_agents(overlay_spec, config)
            agents_list: list = config.get("agents", [])
            for agent_cfg in agents_list:
                agent_id = agent_cfg.get("id", "")
                per_agent = overlay_agents.get(agent_id, {})
                if not per_agent:
                    continue
                ctx_override = per_agent.get("context") or {}
                if isinstance(ctx_override, dict) and ctx_override:
                    merged = dict(agent_cfg.get("context") or {})
                    merged.update(ctx_override)
                    agent_cfg["context"] = merged
                for key in ("design_pattern",):
                    if key in per_agent:
                        agent_cfg[key] = per_agent[key]
                if "tools" in per_agent:
                    existing_tools = list(agent_cfg.get("spec_tools") or [])
                    for t in per_agent["tools"]:
                        if t not in existing_tools:
                            existing_tools.append(t)
                    agent_cfg["spec_tools"] = existing_tools
                if "skills" in per_agent:
                    existing_skills = list(agent_cfg.get("skills") or [])
                    existing_set = set(existing_skills)
                    for sk in per_agent["skills"] or []:
                        if sk not in existing_set:
                            existing_skills.append(sk)
                            existing_set.add(sk)
                    agent_cfg["skills"] = existing_skills
                if "llm" in per_agent:
                    if isinstance(per_agent["llm"], dict):
                        base_llm = agent_cfg.get("llm") or {}
                        base_llm.update(per_agent["llm"])
                        agent_cfg["llm"] = base_llm
                        if "model" in per_agent["llm"]:
                            agent_cfg["llm_model"] = per_agent["llm"]["model"]
                    else:
                        agent_cfg["llm"] = per_agent["llm"]
                if "memory_seed" in per_agent:
                    existing_seed = list(agent_cfg.get("memory_seed") or [])
                    existing_seed.extend(per_agent["memory_seed"] or [])
                    agent_cfg["memory_seed"] = existing_seed

        _apply_overlay_agent_collection_ops(overlay_spec, config, mas_yaml)

        # Global design-pattern override: spec.patch.design_pattern
        dp_spec = overlay_spec.get("design_pattern")
        if dp_spec and config.get("agents"):
            dp_type = dp_spec.get("type", "react") if isinstance(dp_spec, dict) else str(dp_spec)
            dp_params = (dp_spec.get("config") or dp_spec.get("params") or {}) if isinstance(dp_spec, dict) else {}
            for _agent_cfg in config["agents"]:
                _agent_cfg["pattern_framework"] = dp_type
                if dp_params:
                    _agent_cfg.setdefault("pattern_params", {}).update(dp_params)

        if not _apply_control_pipeline(overlay, config, overlay_path):
            _apply_overlay_governance(overlay, overlay_spec, config)
            _apply_overlay_plugin_list(overlay, overlay_spec, config, "observability")
            if overlay:
                _overlay_plugins = overlay.get("spec", {}).get("plugins")
                if _overlay_plugins and config.get("agents"):
                    for _agent_cfg in config["agents"]:
                        _existing = list(_agent_cfg.get("plugins") or [])
                        _agent_cfg["plugins"] = _existing + list(_overlay_plugins)

        # Accumulate raw overlay for cache key (see load_scenario_config for rationale).
        _applied_overlays.append(overlay)

    # Embed all applied overlays so _compute_run_hash sees every dimension.
    config["_overlay_hash_input"] = _applied_overlays
    config.pop("_lab_flavour_spec", None)

    return config, mas_yaml
