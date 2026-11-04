#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


class Hook:
    """Base interface for telemetry hooks."""

    def emit(self, event: Dict[str, Any]) -> None:
        raise NotImplementedError


def _read_current_run_id() -> str | None:
    run_id_file = os.getenv("UI_RUN_ID_FILE", "").strip()
    if not run_id_file:
        return os.getenv("UI_RUN_ID")
    try:
        value = Path(run_id_file).read_text(encoding="utf-8").strip()
    except OSError:
        return os.getenv("UI_RUN_ID")
    return value or None


def _resolve_run_id(event: Dict[str, Any]) -> str | None:
    return event.get("run_id") or _read_current_run_id()


def normalize_event(event: Dict[str, Any], source: str) -> Dict[str, Any]:
    raw_kind = event.get("kind", event.get("type", "unknown"))

    sources: List[str] = []
    if isinstance(event.get("sources"), list):
        sources.extend([str(item) for item in event.get("sources", []) if item is not None])
    sources.append(source)
    deduped_sources: List[str] = []
    seen_sources: set[str] = set()
    for item in sources:
        if item in seen_sources:
            continue
        seen_sources.add(item)
        deduped_sources.append(item)

    tool_name = event.get("tool_name")
    if not tool_name and isinstance(event.get("payload"), dict):
        tool_name = event["payload"].get("tool_name")
    tool_key = str(tool_name or "")
    tool_lower = tool_key.lower()

    arguments = event.get("arguments")
    if arguments is None and isinstance(event.get("payload"), dict):
        arguments = event["payload"].get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    memory_tool = bool(
        tool_key
        and raw_kind in {"tool_call", "tool_result", "tool_unavailable"}
        and (
            tool_lower in {"memory.read", "memory.write", "memory.search", "memory_read", "memory_write", "memory_search"}  # noqa: E501
            or tool_lower.startswith("memory.")
        )
    )

    kind = raw_kind
    target = event.get("target") or event.get("tool_name") or event.get("memory_type")
    if memory_tool:
        memory_type = arguments.get("memory_type") or event.get("memory_type") or "semantic"
        target = memory_type
        if raw_kind == "tool_call":
            kind = "memory_write" if (tool_lower.endswith("write") or tool_lower.endswith("_write")) else "memory_read"
        elif raw_kind == "tool_result":
            kind = "memory_result"
        else:
            kind = "memory_unavailable"

    payload_obj = event.get("payload") if isinstance(event.get("payload"), dict) else None
    usage_obj = payload_obj.get("usage") if isinstance(payload_obj, dict) else None
    usage_total = None
    if isinstance(usage_obj, dict):
        usage_total = usage_obj.get("total_tokens")

    token_count = event.get("token_count")
    if token_count is None and usage_total is not None:
        token_count = usage_total
    volume = event.get("volume")
    if volume is None and token_count is not None:
        volume = token_count

    return {
        "timestamp": event.get("timestamp", time.time()),
        "source": source,
        "sources": deduped_sources,
        "kind": kind,
        "agent_id": event.get("agent_id"),
        "target": target,
        "token_count": token_count,
        "volume": volume,
        "payload": event.get("payload", event),
        "run_id": _resolve_run_id(event),
    }


class UIHook(Hook):
    """Append normalized events to a JSONL feed for the UI."""

    def __init__(self, feed_path: str, source: str) -> None:
        raw_path = Path(feed_path)
        self.feed_root: Path | None = None
        if raw_path.suffix != ".jsonl":
            self.feed_root = raw_path
            self.feed_path = raw_path
        else:
            self.feed_path = raw_path
        self.source = source
        if self.feed_root is None:
            self.feed_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.feed_root.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Dict[str, Any]) -> None:
        normalized = normalize_event(event, self.source)
        feed_path: Path
        if self.feed_root is not None:
            run_id = normalized.get("run_id") or "unknown"
            feed_path = self.feed_root / run_id / "logs" / "ui_feed.jsonl"
            feed_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            feed_path = self.feed_path
        with feed_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized) + "\n")


class CLIHook(Hook):
    """Print normalized events to stdout."""

    def __init__(self, source: str) -> None:
        self.source = source

    def emit(self, event: Dict[str, Any]) -> None:
        normalized = normalize_event(event, self.source)
        print(json.dumps(normalized))


class HookBus:
    """Fan out events to multiple hooks."""

    def __init__(self, hooks: Iterable[Hook]) -> None:
        self.hooks = list(hooks)

    def emit(self, event: Dict[str, Any]) -> None:
        for hook in self.hooks:
            hook.emit(event)

    @classmethod
    def from_env(cls, default_hooks: str, source: str) -> "HookBus":
        hook_list = os.getenv("HOOKS", default_hooks)
        hooks: List[Hook] = []
        for item in [value.strip() for value in hook_list.split(",") if value.strip()]:
            if item == "ui":
                from mas.lab import paths as _paths
                feed_path = os.getenv("UI_FEED_PATH", str(_paths.lab_output() / "ui_feed.jsonl"))
                hooks.append(UIHook(feed_path, source))
            elif item == "cli":
                hooks.append(CLIHook(source))
        return cls(hooks)
