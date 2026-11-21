#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Run Input Envelope — per-run inputs for bench / ctl.

A dataset item is ``inputs`` (what the run receives) + ``expectations`` (what
evaluators compare against). Each slot has a generic layer read by mas-lab and,
where apps need it, a free-form layer they own:

- ``inputs.tool_fixtures`` — see :mod:`mas.lab.inputs.fixtures`
- ``expectations`` — see :mod:`mas.lab.inputs.expectations`
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from mas.lab.inputs.expectations import EXPECTATION_KEYS, resolve_expectations
from mas.lab.inputs.fixtures import resolve_tool_fixtures
from mas.lab.inputs.legacy import is_role_message_shape, legacy_item_to_envelope
from mas.lab.inputs.refs import is_ref_object, resolve_file_slot, resolve_text_slot

__all__ = [
    "EXPECTATION_KEYS",
    "RunInput",
    "fingerprint_run_input",
    "load_run_input",
    "resolve_expectations",
    "resolve_tool_fixtures",
    "run_input_to_dict",
    "validate_run_input_envelope",
]


def _as_messages(value: Any, *, default_role: str = "user") -> List[Dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, str):
        return [{"role": default_role, "content": value}]
    if isinstance(value, dict):
        if "role" in value and "content" in value:
            return [{"role": str(value["role"]), "content": str(value["content"])}]
        for key in ("messages", "turns", "user", "hitl", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                return _as_messages(nested, default_role=default_role)
        if "content" in value:
            return [{"role": str(value.get("role") or default_role), "content": str(value["content"])}]
        raise TypeError(f"cannot coerce mapping to messages: {sorted(value)}")
    if isinstance(value, list):
        out: List[Dict[str, str]] = []
        for item in value:
            out.extend(_as_messages(item, default_role=default_role))
        return out
    raise TypeError(f"messages must be a list, mapping, or string, got {type(value)}")


def _as_seeds(value: Any) -> Optional[List[Dict[str, Any]]]:
    if value is None:
        return None
    if isinstance(value, list):
        return value or None
    if isinstance(value, dict):
        if str(value.get("kind") or "") == "MemorySeed":
            seeds: List[Dict[str, Any]] = []
            for entry in value.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                seed = dict(entry)
                if "source" not in seed and seed.get("key"):
                    seed["source"] = seed["key"]
                if "content" not in seed and seed.get("text"):
                    seed["content"] = seed["text"]
                seeds.append(seed)
            return seeds or None
        for key in ("seeds", "items", "memory_seeds", "entries"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested or None
    raise TypeError(f"memory_seeds must be list, MemorySeed document, or ref, got {type(value)}")


@dataclass
class RunInput:
    user: List[Dict[str, str]]
    hitl: List[Dict[str, str]] = field(default_factory=list)
    turns: List[Dict[str, str]] = field(default_factory=list)
    """Authoritative ordered dialogue; defaults to ``user + hitl`` in ``__post_init__``."""
    memory_seeds: Optional[List[Dict[str, Any]]] = None
    tool_fixtures: Optional[Dict[str, Any]] = None
    """Resolved ``{"by_tool": {tool | "*": payload}}``; see :mod:`mas.lab.inputs.fixtures`."""
    checkpoint_load: Any = None
    checkpoint_save: Any = False
    session_id: Optional[str] = None
    expectations: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.turns:
            self.turns = list(self.user) + list(self.hitl)

    @property
    def primary_prompt(self) -> str:
        for msg in self.dialogue():
            if msg.get("role") == "user" and msg.get("content"):
                return str(msg["content"])
        return str(self.user[0]["content"]) if self.user else ""

    def dialogue(self) -> List[Dict[str, str]]:
        """Ordered user + HITL (+ system) messages for this run."""
        return list(self.turns)

    def all_user_turns(self) -> List[Dict[str, str]]:
        """User messages after the initial prompt (multi-turn)."""
        users = [m for m in self.dialogue() if m.get("role") == "user"]
        return users[1:] if len(users) > 1 else []

    def scripted_queries(self) -> List[str]:
        """Ordered user + HITL messages for SessionController (interleaved)."""
        return [
            str(msg["content"]) for msg in self.dialogue() if msg.get("role") in {"user", "hitl"} and msg.get("content")
        ]


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _scenario_block(scenario: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not scenario:
        return {}
    block: Dict[str, Any] = {}
    if isinstance(scenario.get("inputs"), dict):
        block["inputs"] = dict(scenario["inputs"])
    if isinstance(scenario.get("expectations"), dict):
        block["expectations"] = dict(scenario["expectations"])
    spec = scenario.get("spec") or {}
    if spec.get("memory_seed") is not None:
        block.setdefault("inputs", {})["memory_seeds"] = spec["memory_seed"]
    return block


def _experiment_defaults(experiment: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not experiment:
        return {}
    spec = experiment.get("spec") or experiment
    defaults = spec.get("defaults") or {}
    out: Dict[str, Any] = {}
    if isinstance(defaults.get("inputs"), dict):
        out["inputs"] = dict(defaults["inputs"])
    if isinstance(defaults.get("expectations"), dict):
        out["expectations"] = dict(defaults["expectations"])
    return out


def _compose_dialogue(
    user: List[Dict[str, str]],
    hitl: List[Dict[str, str]],
) -> tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
    """Re-split by role: legacy-coerced inputs may mix roles within either list."""
    dialogue = list(user) + list(hitl)
    users = [m for m in dialogue if m.get("role") == "user"]
    hitls = [m for m in dialogue if m.get("role") == "hitl"]
    return users, hitls, dialogue


def load_run_input(
    item: Dict[str, Any],
    *,
    scenario: Optional[Dict[str, Any]] = None,
    experiment: Optional[Dict[str, Any]] = None,
    base_path: Optional[Path] = None,
    source: Optional[Path] = None,
) -> RunInput:
    """Merge experiment → scenario → item envelope into :class:`RunInput`."""
    where = str(source or base_path or f"item {item.get('id')!r}")
    item = legacy_item_to_envelope(item, where=where)
    merged: Dict[str, Any] = {}
    merged = _deep_merge_dict(merged, _experiment_defaults(experiment))
    merged = _deep_merge_dict(merged, _scenario_block(scenario))
    merged = _deep_merge_dict(
        merged,
        {"inputs": dict(item["inputs"]), "expectations": dict(item.get("expectations") or {})},
    )

    inputs = merged["inputs"]
    if is_role_message_shape(inputs.get("user")):
        from mas.lab.deprecations import warn_deprecated

        warn_deprecated("dataset.role_list_user", where=where)
    user = _as_messages(resolve_text_slot(inputs.get("user"), base_path), default_role="user")
    hitl = _as_messages(resolve_text_slot(inputs.get("hitl"), base_path), default_role="hitl")
    user, hitl, dialogue = _compose_dialogue(user, hitl)
    if not user:
        user = [{"role": "user", "content": ""}]
        dialogue = list(user) + list(hitl)

    memory_raw = inputs.get("memory_seeds")
    if isinstance(memory_raw, str) or is_ref_object(memory_raw):
        memory_raw = resolve_file_slot(memory_raw, base_path)
    memory_seeds = _as_seeds(memory_raw) if memory_raw is not None else None

    checkpoint = inputs.get("checkpoint") or {}
    if not isinstance(checkpoint, dict):
        raise TypeError(f"{where}: inputs.checkpoint must be a mapping")
    load_val = checkpoint.get("load")
    checkpoint_load = (
        resolve_file_slot(load_val, base_path) if isinstance(load_val, str) or is_ref_object(load_val) else load_val
    )

    return RunInput(
        user=user,
        hitl=hitl,
        turns=dialogue,
        memory_seeds=memory_seeds,
        tool_fixtures=resolve_tool_fixtures(inputs.get("tool_fixtures"), base_path),
        checkpoint_load=checkpoint_load,
        checkpoint_save=checkpoint.get("save", False),
        session_id=inputs.get("session_id"),
        expectations=resolve_expectations(merged.get("expectations"), base_path, where=where),
        environment=dict(item.get("environment") or {}),
    )


def run_input_to_dict(run_input: RunInput) -> Dict[str, Any]:
    user_texts = [str(m.get("content") or "") for m in run_input.user]
    inputs: Dict[str, Any] = {"user": user_texts[0] if len(user_texts) == 1 else user_texts}
    if run_input.hitl:
        inputs["hitl"] = [str(m.get("content") or "") for m in run_input.hitl]
    if run_input.memory_seeds is not None:
        inputs["memory_seeds"] = run_input.memory_seeds
    if run_input.tool_fixtures is not None:
        inputs["tool_fixtures"] = run_input.tool_fixtures
    if run_input.session_id:
        inputs["session_id"] = run_input.session_id
    if run_input.checkpoint_load is not None or run_input.checkpoint_save:
        inputs["checkpoint"] = {"load": run_input.checkpoint_load, "save": run_input.checkpoint_save}
    out: Dict[str, Any] = {"inputs": inputs}
    if run_input.expectations:
        out["expectations"] = run_input.expectations
    if run_input.environment:
        out["environment"] = dict(run_input.environment)
    return out


def fingerprint_run_input(run_input: RunInput) -> str:
    """Stable hash for trace-cache ``inputs.json``."""
    payload = json.dumps(run_input_to_dict(run_input), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_run_input_envelope(data: Dict[str, Any]) -> None:
    from mas.lab.schemas.validate import validate_against_lab_schema

    validate_against_lab_schema("run-input", data)
