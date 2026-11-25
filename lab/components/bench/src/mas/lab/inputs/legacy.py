#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pre-envelope dataset items (``prompt``, top-level ``turns`` / ground truth)."""

from __future__ import annotations

from typing import Any, Dict, List

_LEGACY_PROMPT_KEYS = ("prompt", "query", "question", "text", "input")
_LEGACY_TOP_KEYS = ("turns", "memory_seeds", "session_id", "expected_answer", "ground_truth")


def is_role_message_shape(value: Any) -> bool:
    """True when ``user`` is OpenAI-style ``{role, content}`` instead of a string."""
    if isinstance(value, dict) and "role" in value and "content" in value:
        return True
    if isinstance(value, list):
        return any(isinstance(item, dict) and "role" in item and "content" in item for item in value)
    return False


def _is_legacy_item(item: Dict[str, Any]) -> bool:
    existing = item.get("inputs")
    envelope = isinstance(existing, dict) and existing.get("user") is not None
    if any(item.get(key) is not None for key in _LEGACY_PROMPT_KEYS):
        return True
    if isinstance(item.get("user"), (str, list)) and not envelope:
        return True
    return any(item.get(key) is not None for key in _LEGACY_TOP_KEYS) and not envelope


def legacy_item_to_envelope(item: Dict[str, Any], *, where: str = "dataset item") -> Dict[str, Any]:
    """Rewrite a pre-envelope item to ``inputs`` + ``expectations`` (warns)."""
    if _is_legacy_item(item):
        from mas.lab.deprecations import warn_deprecated

        warn_deprecated("dataset.legacy_item", where=where)
    existing = item.get("inputs")
    if isinstance(existing, dict) and existing.get("user") is not None:
        return item
    prompt: Any = existing.get("user") if isinstance(existing, dict) else None
    if prompt is None:
        prompt = next((item[k] for k in _LEGACY_PROMPT_KEYS if item.get(k) is not None), None)
    if prompt is None and isinstance(item.get("user"), (str, list)):
        prompt = item["user"]
    if prompt is None:
        prompt = ""
    inputs: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    inputs["user"] = prompt
    hitl: List[Any] = []
    extra_users: List[Any] = []
    for turn in item.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "user")
        content = turn.get("content")
        if isinstance(content, list):
            stored: Any = content
        else:
            stored = str(content or "")
        if role == "hitl":
            hitl.append(stored)
        elif role == "user" and stored:
            extra_users.append(stored)
    if extra_users:
        inputs["user"] = [prompt if isinstance(prompt, str) else str(prompt), *extra_users]
    if hitl:
        inputs["hitl"] = hitl
    if item.get("memory_seeds") is not None:
        inputs["memory_seeds"] = item["memory_seeds"]
    if item.get("session_id"):
        inputs["session_id"] = item["session_id"]
    legacy_keys = set(_LEGACY_PROMPT_KEYS) | set(_LEGACY_TOP_KEYS) | {"user"}
    out = {k: v for k, v in item.items() if k not in legacy_keys}
    out["inputs"] = inputs
    gt = item.get("ground_truth") or item.get("expected_answer")
    if gt is not None:
        out["expectations"] = {**dict(item.get("expectations") or {}), "ground_truth": gt}
    return out
