#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ControllerContract for MAS deployment lifecycle management.

Implementations are distributed as optional plugin packages and discovered
through the entry-point group ``mas.lab.controller.plugins``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Type


@dataclass
class AgentSnapshot:
    """Live state of one deployed agent slot."""

    agent_id: str
    status: str
    model: str = ""
    replicas: int = 1
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InfraSnapshot:
    """Point-in-time view of deployment health and capacity."""

    healthy: bool
    agents: List[AgentSnapshot] = field(default_factory=list)
    capacity: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def running_agents(self) -> List[AgentSnapshot]:
        return [a for a in self.agents if a.status == "running"]


@dataclass
class DeployResult:
    """Result of a deployment operation."""

    success: bool
    agent_id: str
    message: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


class ControllerContract(ABC):
    """Abstract controller interface for MAS infrastructure backends."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def probe(self) -> InfraSnapshot:
        """Return current deployment health snapshot."""

    @abstractmethod
    def deploy(self, manifest: Dict[str, Any], **kwargs: Any) -> DeployResult:
        """Deploy or update agents from a manifest."""

    @abstractmethod
    def scale(self, agent_id: str, replicas: int, **kwargs: Any) -> None:
        """Scale an agent slot."""

    @abstractmethod
    def configure(self, agent_id: str, config: Dict[str, Any], **kwargs: Any) -> None:
        """Apply runtime configuration to a deployed agent."""

    @abstractmethod
    def teardown(self, agent_id: str, **kwargs: Any) -> None:
        """Remove a deployed agent."""

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": [
                method
                for method in ("deploy", "scale", "configure", "teardown")
                if type(self).__dict__.get(method) is not ControllerContract.__dict__[method]
            ],
        }


_CONTROLLER_GROUP = "mas.lab.controller.plugins"


def discover_controllers() -> Dict[str, Type[ControllerContract]]:
    """Discover installed controller plugins via entry points."""

    from importlib.metadata import entry_points

    result: Dict[str, Type[ControllerContract]] = {}
    for entry in entry_points(group=_CONTROLLER_GROUP):
        try:
            result[entry.name] = entry.load()
        except Exception:
            continue
    return result


def get_controller(name: str) -> ControllerContract:
    """Instantiate one controller plugin by name."""

    plugins = discover_controllers()
    if name not in plugins:
        installed = list(plugins.keys()) or ["(none)"]
        raise KeyError(
            f"Controller plugin {name!r} not found. Installed: {installed}."
        )
    return plugins[name]()
