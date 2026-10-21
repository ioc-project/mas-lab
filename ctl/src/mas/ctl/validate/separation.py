#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Model/access separation rules — ported from mas-lab v1 validator step 3."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

# Keep in sync with mas.ctl.overlay.canvas.CANVAS_EXTENSION_KEYS.
_CANVAS_EXTENSION_KEYS = ("x-canvas-positions", "x-canvas-node-ids", "x-text-input")


def _is_set(val: Any) -> bool:
    return val is not None and val != "" and val is not False


def _iter_paths(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            yield from _iter_paths(v, p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _iter_paths(item, f"{prefix}[{i}]")
    else:
        yield prefix, obj


class SeparationValidator(ABC):
    kind: ClassVar[str]

    @classmethod
    def collect_violations(cls, data: dict[str, Any]) -> list[str]:
        return cls._collect_violations(data)

    @classmethod
    @abstractmethod
    def _collect_violations(cls, data: dict[str, Any]) -> list[str]:
        ...


class FlavourSeparationValidator(SeparationValidator):
    kind = "flavour"

    # FT4: a flavour is deployment posture only — these moved to kind: Agent
    # (llm, skills). Offline LLM is llm_cache replay, not a Flavour field.
    # See docs/design/flavour-boundary.md.
    _FORBIDDEN_BLOCKS: ClassVar[dict[str, str]] = {
        "llm": "spec.llm belongs in kind: Agent (spec.models), not Flavour",
        "skills": "spec.skills belongs in kind: Agent, not Flavour",
        "prefer_local": "spec.prefer_local belongs in workspace infra_refs / runtime tuning, not Flavour",
    }

    @classmethod
    def _collect_violations(cls, data: dict[str, Any]) -> list[str]:
        spec = data.get("spec", {}) or {}
        violations: list[str] = []
        for key, message in cls._FORBIDDEN_BLOCKS.items():
            if spec.get(key):
                violations.append(message)
        if spec.get("infra_refs"):
            violations.append("spec.infra_refs is forbidden in Flavour — use workspace or --infra-ref")
        if _is_set(spec.get("infra_ref")):
            violations.append("spec.infra_ref is forbidden in Flavour — use workspace or --infra-ref")
        return violations


class AgentSeparationValidator(SeparationValidator):
    kind = "agent"

    @classmethod
    def _collect_violations(cls, data: dict[str, Any]) -> list[str]:
        spec = data.get("spec", {}) or {}
        violations: list[str] = []
        if spec.get("execution"):
            violations.append(
                "spec.execution is forbidden on Agent — use workspace runtime_refs, "
                "$XDG_CONFIG_HOME/mas/runtime/, or --runtime-ref"
            )
        if spec.get("runtime_refs") or spec.get("runtime_ref"):
            violations.append(
                "spec.runtime_refs is forbidden on Agent — use workspace config or CLI"
            )
        if spec.get("infra_refs") or spec.get("infra_ref"):
            violations.append(
                "spec.infra_refs is forbidden on Agent — use workspace config.yaml or --infra-ref"
            )
        if spec.get("infra_interceptors") or spec.get("infra_interceptor"):
            violations.append(
                "spec.infra_interceptors is forbidden on Agent — use workspace config or CLI"
            )
        return violations


class MASSeparationValidator(SeparationValidator):
    kind = "mas"
    _ACCESS_KEYS: ClassVar[frozenset[str]] = frozenset({"api_base", "api_key_env"})

    @classmethod
    def _collect_violations(cls, data: dict[str, Any]) -> list[str]:
        violations: list[str] = []
        spec = data.get("spec", {}) or {}
        if spec.get("runtime_refs") or spec.get("runtime_ref"):
            violations.append(
                "spec.runtime_refs is forbidden on MAS — use workspace config or CLI"
            )
        if spec.get("infra_refs") or spec.get("infra_ref"):
            violations.append(
                "spec.infra_refs is forbidden on MAS — use workspace config.yaml or --infra-ref"
            )
        if spec.get("infra_interceptors") or spec.get("infra_interceptor"):
            violations.append(
                "spec.infra_interceptors is forbidden on MAS — use workspace config or CLI"
            )
        for path, val in _iter_paths(data):
            if path.startswith("spec.agency.agents["):
                continue
            key = path.rsplit(".", 1)[-1].split("[")[0]
            if key in cls._ACCESS_KEYS and _is_set(val):
                violations.append(f"{path} is an access concern — move to flavour/infra bundle")
            if key == "model" and _is_set(val):
                violations.append(f"{path} is model-selection — move to agent spec.models")
        return violations


class OverlaySeparationValidator(MASSeparationValidator):
    kind = "overlay"

    @classmethod
    def _collect_violations(cls, data: dict[str, Any]) -> list[str]:
        violations: list[str] = []
        spec = data.get("spec") or {}
        target_kind = str((spec.get("target") or {}).get("kind") or "").strip().lower()
        patch = spec.get("patch") if isinstance(spec.get("patch"), dict) else {}
        if spec.get("runtime_refs") or spec.get("runtime_ref"):
            violations.append(
                "spec.runtime_refs is forbidden on Overlay — use workspace config or CLI"
            )
        if spec.get("infra_refs") or spec.get("infra_ref"):
            violations.append(
                "spec.infra_refs is forbidden on Overlay — use workspace config.yaml or --infra-ref"
            )
        if spec.get("infra_interceptors") or spec.get("infra_interceptor"):
            violations.append(
                "spec.infra_interceptors is forbidden on Overlay — use workspace config or CLI"
            )
        for path, val in _iter_paths(data):
            if "agency.agents[" in path:
                continue
            if any(path == key or path.startswith(f"{key}.") for key in _CANVAS_EXTENSION_KEYS):
                continue
            key = path.rsplit(".", 1)[-1].split("[")[0]
            if key in cls._ACCESS_KEYS and _is_set(val):
                violations.append(f"{path} is an access concern — move to flavour/infra bundle")
            if key == "model" and _is_set(val):
                if target_kind == "agent" and path.startswith("spec.patch"):
                    continue
                violations.append(f"{path} is model-selection — move to agent spec.models")
        if isinstance(patch, dict):
            if patch.get("execution"):
                violations.append(
                    "spec.patch.execution is forbidden — use workspace runtime_refs or --runtime-ref"
                )
            if patch.get("runtime_refs") or patch.get("runtime_ref"):
                violations.append(
                    "spec.patch.runtime_refs is forbidden — use workspace config or CLI"
                )
            if patch.get("infra_refs") or patch.get("infra_ref"):
                violations.append(
                    "spec.patch.infra_refs is forbidden — use workspace config or CLI"
                )
            if patch.get("infra_interceptors") or patch.get("infra_interceptor"):
                violations.append(
                    "spec.patch.infra_interceptors is forbidden — use workspace config or CLI"
                )
        return violations


class PlacementPlanSeparationValidator(SeparationValidator):
    kind = "placement_plan"

    @classmethod
    def _collect_violations(cls, data: dict[str, Any]) -> list[str]:
        violations: list[str] = []
        spec = data.get("spec") or {}
        for agent in spec.get("agents") or []:
            if not isinstance(agent, dict):
                continue
            aid = agent.get("id", "?")
            if "runtime" in agent and isinstance(agent["runtime"], dict):
                if "pattern_plugin_id" in agent["runtime"]:
                    violations.append(
                        f"spec.agents[{aid!r}].runtime.pattern_plugin_id belongs in "
                        "EffectiveBind, not PlacementPlan"
                    )
            if "engine" in agent:
                violations.append(
                    f"spec.agents[{aid!r}].engine belongs in EffectiveBind (infra bind), not PlacementPlan"
                )
        return violations


_SEPARATION: dict[str, type[SeparationValidator]] = {
    "agent": AgentSeparationValidator,
    "flavour": FlavourSeparationValidator,
    "mas": MASSeparationValidator,
    "overlay": OverlaySeparationValidator,
    "patch": OverlaySeparationValidator,
    "placement_plan": PlacementPlanSeparationValidator,
}


def check_separation(data: dict[str, Any], kind: str | None) -> list[str]:
    if not kind:
        return []
    validator = _SEPARATION.get(kind.lower())
    if validator is None:
        return []
    return validator.collect_violations(data)
