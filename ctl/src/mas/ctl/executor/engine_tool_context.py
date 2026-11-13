#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Capability-scoped facade handed to engine-tool plugins.

Plugins never receive the materialized run or the comm bus. They get this
object, whose methods perform the governed action themselves — so a
third-party plugin is subject to the same spawn ledger as a shipped one
without having to remember to check it.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mas.ctl.executor.spawn_ledger import SpawnLedger
from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
from mas.runtime.boundary.engine_tools import EngineToolBudgetExceeded
from mas.runtime.driver.instance import RuntimeInstance

logger = logging.getLogger(__name__)


class EngineToolTurnFailed(RuntimeError):
    """A nested turn ran but did not produce a usable answer."""


@dataclass
class _Child:
    instance: RuntimeInstance
    shared_obs_plugin_set: Any = None


@dataclass
class MaterializedEngineToolContext:
    """`EngineToolContext` over a materialized run, enforcing the spawn ledger."""

    materialized: Any
    session_id: str = ""
    parent_agent_id: str = ""
    ledger: SpawnLedger = field(default_factory=SpawnLedger)
    working_memory_registry: WorkingMemoryRegistry = field(default_factory=WorkingMemoryRegistry)
    display: Any = None
    verbose: int = 0
    instance_factory: Callable[[dict[str, Any], Path | None, str, str], RuntimeInstance] | None = None
    controller_factory: Callable[..., Any] | None = None
    flavour_spec: dict[str, Any] | None = None
    parent_spec: dict[str, Any] | None = None
    entry_id: str = ""
    delegate_ids: tuple[str, ...] = ()
    _children: dict[str, _Child] = field(default_factory=dict, init=False, repr=False)

    @property
    def depth(self) -> int:
        return self.ledger.agent_depth(self.session_id, self.parent_agent_id)

    def spawn_instance(
        self,
        manifest: dict[str, Any],
        *,
        template_id: str,
        manifest_dir: Path | None = None,
    ) -> str:
        """Materialize one child and return its minted id."""
        if not self.ledger.allow_spawn(self.session_id, self.parent_agent_id):
            raise EngineToolBudgetExceeded("depth or spawn-count budget exceeded")
        child_id = self.ledger.mint_agent_id(self.parent_agent_id, template_id)
        instances = self.materialized.instances
        if child_id in instances:
            raise RuntimeError(f"subagent id collision: {child_id}")

        factory = self.instance_factory or self._instantiate_child
        instance = factory(manifest, manifest_dir, child_id, template_id)
        instance.driver.agent_id = child_id
        driver_ctx = getattr(instance.driver, "ctx", None)
        if driver_ctx is not None:
            driver_ctx.session_id = self.session_id
            driver_ctx.agent_id = child_id
        instances[child_id] = instance

        parent_instance = instances.get(self.parent_agent_id)
        shared_obs_plugin_set = getattr(parent_instance, "obs_plugin_set", None)
        if shared_obs_plugin_set is not None and instance.obs_plugin_set is None:
            from mas.runtime.boundary.obs.plugins import attach_observability_plugin_set

            attach_observability_plugin_set(shared_obs_plugin_set, instance, agent_id=child_id, begin_run=False)
        bus = getattr(self.materialized, "bus", None)
        if bus is not None:
            from mas.ctl.placement.bus.adapter import RuntimeCommEndpoint

            bus.register(child_id, RuntimeCommEndpoint(child_id, instance))

        if manifest_dir is not None:
            from mas.ctl.executor.subagent_spawner import wire_subagent_spawning

            wire_subagent_spawning(
                getattr(instance.driver, "engine", None),
                materialized=self.materialized,
                manifest=manifest,
                manifest_dir=manifest_dir,
                parent_agent_id=child_id,
                session_id=self.session_id,
                working_memory_registry=self.working_memory_registry,
                ledger=self.ledger,
                display=self.display,
                verbose=self.verbose,
                flavour_spec=self.flavour_spec,
                parent_spec=self.parent_spec,
                entry_id=self.entry_id,
                delegate_ids=self.delegate_ids,
            )

        self.ledger.enter(self.session_id, child_agent_id=child_id, parent_agent_id=self.parent_agent_id)
        self._children[child_id] = _Child(instance, shared_obs_plugin_set)
        parent_ctx = (
            getattr(getattr(parent_instance, "driver", None), "ctx", None) if parent_instance is not None else None
        )
        spec_log = getattr(self, "spec_log", None) or getattr(parent_ctx, "spec_log", None)
        if spec_log is not None:
            latest = spec_log.latest(self.session_id)
            if latest is not None:
                instance.spec_revision = latest.revision
                if driver_ctx is not None:
                    driver_ctx.spec_revision = latest.revision
                    driver_ctx.spec_log = spec_log
                    driver_ctx.current_spec = spec_log.current_spec(self.session_id)
        return child_id

    def run_turn(
        self,
        agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        """Run a single turn on one agent of this run."""
        instance = self.materialized.instances.get(agent_id)
        if instance is None:
            raise EngineToolTurnFailed(f"unknown agent {agent_id!r}")

        from mas.ctl.session.controller import ConversationConfig, SessionController
        from mas.ctl.ui.turn_result import turn_failed, turn_failure_message

        controller_factory = self.controller_factory or SessionController
        controller = controller_factory(
            instance=instance,
            display=self.display,
            verbose=self.verbose,
            agent_id=agent_id,
            config=ConversationConfig(single_turn=True),
            session_id=self.session_id,
            working_memory_key=self.session_id,
            working_memory_registry=self.working_memory_registry,
            caller_agent_id=self.parent_agent_id,
        )
        result = controller.run_turn(task, turn_id=f"{agent_id}-spawn", parent_call_id=caller_call_id)
        if turn_failed(result):
            raise EngineToolTurnFailed(turn_failure_message(result, agent_id))
        return result.text

    async def arun_turn(
        self,
        agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
    ) -> str:
        """Async twin of :meth:`run_turn`."""
        instance = self.materialized.instances.get(agent_id)
        if instance is None:
            raise EngineToolTurnFailed(f"unknown agent {agent_id!r}")

        from mas.ctl.session.controller import ConversationConfig, SessionController
        from mas.ctl.ui.turn_result import turn_failed, turn_failure_message

        controller_factory = self.controller_factory or SessionController
        controller = controller_factory(
            instance=instance,
            display=self.display,
            verbose=self.verbose,
            agent_id=agent_id,
            config=ConversationConfig(single_turn=True),
            session_id=self.session_id,
            working_memory_key=self.session_id,
            working_memory_registry=self.working_memory_registry,
            caller_agent_id=self.parent_agent_id,
        )
        arun = getattr(controller, "arun_turn", None)
        if callable(arun):
            result = await arun(task, turn_id=f"{agent_id}-spawn", parent_call_id=caller_call_id)
        else:
            result = controller.run_turn(task, turn_id=f"{agent_id}-spawn", parent_call_id=caller_call_id)
        if turn_failed(result):
            raise EngineToolTurnFailed(turn_failure_message(result, agent_id))
        return result.text

    def teardown_instance(self, agent_id: str) -> None:
        """Release one child: bus, instance table, working memory, observability, ledger."""
        child = self._children.pop(agent_id, None)
        if child is None:
            return
        bus = getattr(self.materialized, "bus", None)
        unregister = getattr(bus, "unregister", None) if bus is not None else None
        if callable(unregister):
            unregister(agent_id)
        self.materialized.instances.pop(agent_id, None)
        self.working_memory_registry.drop(self.session_id, agent_id)
        plugin_set = getattr(child.instance, "obs_plugin_set", None)
        if plugin_set is not None and plugin_set is not child.shared_obs_plugin_set:
            plugin_set.close()
        self.ledger.exit(self.session_id, child_agent_id=agent_id)

    def _instantiate_child(
        self, manifest: dict[str, Any], manifest_dir: Path | None, child_id: str, _template_id: str
    ) -> RuntimeInstance:
        from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime

        instance, _store = instantiate_runtime(
            InstantiationOptions(
                agent_manifest=copy.deepcopy(manifest),
                manifest_dir=manifest_dir,
                app_root=manifest_dir,
                enable_observability=True,
                flavour_spec=self.flavour_spec,
                parent_spec=self.parent_spec,
                entry_id=self.entry_id or None,
                delegate_ids=self.delegate_ids,
            )
        )
        instance.driver.agent_id = child_id
        return instance
