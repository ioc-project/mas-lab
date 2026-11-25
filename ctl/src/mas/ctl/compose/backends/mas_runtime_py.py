#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Default OSS Python runtime — RuntimeInstance from EffectiveBind + resolved infra."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mas.ctl.compose.models import AgentBindSlice, EffectiveBindManifest, KernelBackendId, ResolvedInfra
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID
from mas.runtime.driver.instance import RuntimeInstance


@dataclass
class MaterializedLocal:
    instances: dict[str, RuntimeInstance] = field(default_factory=dict)
    infra: ResolvedInfra | None = None


class MasRuntimePyKernelBackend:
    backend_id: KernelBackendId = DEFAULT_RUNTIME_ID  # type: ignore[assignment]

    def __init__(self, *, resolved_infra: ResolvedInfra | None = None) -> None:
        self._infra = resolved_infra

    def create_agent_runtime(self, bind: EffectiveBindManifest, agent_id: str) -> RuntimeInstance:
        from mas.ctl.session.bootstrap import (
            InstantiationOptions,
            hitl_contract_for_mode,
            instantiate_runtime,
        )

        slice_ = _find_agent(bind, agent_id)
        manifest_path = slice_.manifest_path
        agent_manifest: dict | None = None
        manifest_dir = bind.mas_base_dir or Path.cwd()
        if manifest_path:
            mp = Path(manifest_path)
            if not mp.is_absolute():
                base = bind.mas_base_dir or Path.cwd()
                mp = (base / mp).resolve()
            if mp.is_file():
                from mas.runtime.secrets import load_checked_document

                agent_manifest = load_checked_document(mp)
                manifest_dir = mp.parent
                if agent_manifest:
                    from mas.ctl.manifest.mas_agent_merge import apply_loaded_agent_patch
                    from mas.runtime.engine.tools import resolve_manifest_tool_refs

                    composed = bind.composed_application
                    mas_config = composed.config if composed is not None else None
                    patches = composed.agent_patches if composed is not None else {}
                    agent_manifest = apply_loaded_agent_patch(
                        agent_manifest,
                        agent_id=agent_id,
                        agent_patches=patches,
                        mas_config=mas_config,
                    )
                    # The runtime reads an agent's own next hops (`spec.communication.peers`), not the MAS
                    # workflow, so a design pattern that sends (deterministic_parallel, ...) finds its peers.
                    if isinstance(mas_config, dict):
                        mas_spec = mas_config.get("spec") if isinstance(mas_config.get("spec"), dict) else mas_config
                        if isinstance(mas_spec, dict):
                            wf = mas_spec.get("workflow")
                            if isinstance(wf, dict):
                                from mas.ctl.manifest.mas_agent_merge import compile_agent_spec

                                compile_agent_spec(agent_manifest, wf, agent_id=agent_id)
                            agency = mas_spec.get("agency")
                            if isinstance(agency, dict) and isinstance(agency.get("agents"), list):
                                agent_manifest.setdefault("spec", {})["agency"] = {
                                    "agents": list(agency.get("agents") or [])
                                }
                    agent_manifest = resolve_manifest_tool_refs(agent_manifest, manifest_dir)

        parent_model = None
        mas_config = bind.composed_application.config if bind.composed_application is not None else None
        if isinstance(mas_config, dict):
            from mas.runtime.spec.model_ref import primary_model_string

            parent_model = primary_model_string(mas_config)

        instance, _ = instantiate_runtime(
            InstantiationOptions(
                pattern_plugin_id=slice_.pattern_plugin_id,
                agent_manifest=agent_manifest,
                manifest_dir=manifest_dir,
                app_root=bind.mas_base_dir,
                resolved_infra=self._infra or ResolvedInfra(),
                parent_default_model=parent_model,
                experiment_default_model=bind.experiment_default_model,
                model_override=bind.model_override,
                experiment_model_slots=bind.experiment_model_slots,
                parent_spec=mas_config if isinstance(mas_config, dict) else None,
                hitl_contract=hitl_contract_for_mode(bind.hitl_mode),
                flavour_spec=bind.flavour_spec,
                obs_config=bind.obs_config,
                obs_attach_plugins=bind.obs_attach_plugins,
            ),
        )
        instance.driver.agent_id = agent_id
        return instance

    def materialize(
        self,
        bind: EffectiveBindManifest,
        plan,
        *,
        resolved_infra: ResolvedInfra | None = None,
    ) -> MaterializedLocal:
        infra = resolved_infra or self._infra
        backend = MasRuntimePyKernelBackend(resolved_infra=infra)
        instances = {a.agent_id: backend.create_agent_runtime(bind, a.agent_id) for a in bind.agents}
        return MaterializedLocal(instances=instances, infra=infra)


def _find_agent(bind: EffectiveBindManifest, agent_id: str) -> AgentBindSlice:
    for agent in bind.agents:
        if agent.agent_id == agent_id:
            return agent
    raise KeyError(f"agent {agent_id!r} not in effective bind")
