#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared LLM response cache utilities (live engine and llm_cache middleware)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any


def resolve_cache_path(cache_path: str | Path | None = None) -> Path:
    """Explicit ``cache_path`` wins, then ``MAS_LLM_CACHE``, then the shared
    XDG cache root (``$XDG_CACHE_HOME/mas/llm_cache.json`` — same convention
    as the trace/artifacts caches in ``mas.runtime.xdg``)."""
    if cache_path:
        return Path(cache_path).expanduser().resolve()
    env = os.environ.get("MAS_LLM_CACHE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from mas.runtime.xdg import mas_cache_root

    return (mas_cache_root() / "llm_cache.json").resolve()


def load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def persist_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def legacy_preview_cache_key(preview: str) -> str:
    """Pre-routing middleware key: SHA-256 of the preview only."""
    return hashlib.sha256(preview.encode("utf-8")).hexdigest()


def bind_route_cache_key(
    preview: str,
    *,
    leaf: str = "",
    model: str = "",
    api_base: str = "",
) -> str:
    """SHA-256 of the assembled preview plus the routed leaf and model.

    A common cache file is shared across leaves; without the route, two
    models (or two hosts) replay each other's answer for the same prompt.
    """
    blob = f"leaf={leaf}\nmodel={model}\napi_base={api_base}\n{preview}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def lookup_preview_cache(
    cache: dict[str, Any],
    preview: str,
    *,
    leaf: str = "",
    model: str = "",
    api_base: str = "",
    leaf_count: int = 1,
) -> tuple[str, Any | None]:
    """Return ``(write_key, entry)``.

    Lookup uses the routed key first. With a single leaf, a miss falls back
    to the pre-routing preview hash so existing cache files still hit. With
    more than one leaf the old key is ignored (it has no leaf, so it could
    cross hosts). Writes always use the routed key. There is no version field.
    """
    write_key = bind_route_cache_key(preview, leaf=leaf, model=model, api_base=api_base)
    if write_key in cache:
        return write_key, cache[write_key]
    if leaf_count > 1:
        return write_key, None
    old_key = legacy_preview_cache_key(preview)
    if old_key in cache:
        return write_key, cache[old_key]
    return write_key, None


def cache_route_from_engine(engine: Any) -> tuple[str, str, str, int]:
    """Return ``(leaf_name, canonical_model, api_base, leaf_count)`` for a cache key."""
    from mas.runtime.engine.infra_pipeline import call_api_base, call_model, call_proxies

    model = call_model(engine)
    leaves, proxy = call_proxies(engine)
    api_base = call_api_base(engine)
    leaf_name = ""
    if leaves and model:
        from mas.runtime.registry.llm_provider_registry import canonical_model_name, leaf_for_model

        try:
            leaf = leaf_for_model(model, leaves, default=proxy or None)
        except (KeyError, ValueError):
            leaf = None
        if leaf:
            model = canonical_model_name(model, leaf) or model
            api_base = str(leaf.get("api_base") or api_base)
            named = str(leaf.get("name") or "").strip()
            if named:
                leaf_name = named
            else:
                for name, item in leaves.items():
                    if item.get("api_base") == leaf.get("api_base") and item.get("api_key_env") == leaf.get(
                        "api_key_env"
                    ):
                        leaf_name = str(name)
                        break
    leaf_count = len(leaves) if leaves else 1
    return leaf_name, model, api_base, leaf_count


def llm_cache_key(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    params: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {"model": model, "messages": messages, "tools": tools or []}
    if params:
        payload["params"] = params
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def completion_cache_params(
    *,
    temperature: float,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Stable extra fields that distinguish otherwise-identical completions."""
    params: dict[str, Any] = {"temperature": temperature}
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    for key in ("max_completion_tokens", "reasoning", "reasoning_effort", "sampling", "extra_body"):
        value = kwargs.get(key)
        if value is not None:
            params[key] = value
    return params


def lookup_response(
    cache: dict[str, Any],
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any] | None, str]:
    """Return ``(content, usage, source)`` for a cached completion, if any."""
    key = llm_cache_key(model, messages, tools, params=params)
    entry = cache.get(key)
    if isinstance(entry, str) and entry.strip():
        return entry, None, "cache"
    if isinstance(entry, dict):
        content = entry.get("content")
        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else None
        if isinstance(content, str) and content.strip():
            return content, usage, str(entry.get("source") or "cache")
        tool_calls = entry.get("tool_calls")
        if tool_calls:
            return json.dumps({"tool_calls": tool_calls}), usage, "cache"
    return None, None, ""


def last_user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def middleware_cache_serialize(
    ret: Any,
    *,
    include_preview: bool = False,
    preview: str = "",
) -> str | dict[str, Any]:
    """Serialize ``EngineIoReturn`` for ``LlmCacheMiddleware`` disk storage.

    Plain strings remain valid for simple STOP text (backward compatible).
    Tool-call and parallel-tool responses are stored as structured dicts.
    """
    next_step = str(getattr(ret, "next_step", "STOP") or "STOP")
    text = str(getattr(ret, "text", "") or "")
    tool_name = str(getattr(ret, "tool_name", "") or "")
    parallel_tools = tuple(getattr(ret, "parallel_tools", ()) or ())
    usage = dict(getattr(ret, "usage", {}) or {})
    finish_reason = str(getattr(ret, "finish_reason", "") or "")

    if next_step == "STOP" and text and not tool_name and not parallel_tools and not include_preview and not usage:
        return text

    entry: dict[str, Any] = {
        "next_step": next_step,
        "text": text,
        "usage": usage,
        "finish_reason": finish_reason,
    }
    if tool_name:
        entry["tool_name"] = tool_name
        entry["tool_arguments"] = dict(getattr(ret, "tool_arguments", {}) or {})
    if parallel_tools:
        entry["parallel_tools"] = [
            {
                "tool_name": str(spec.tool_name),
                "tool_arguments": dict(spec.tool_arguments or {}),
            }
            for spec in parallel_tools
        ]
    if include_preview and preview.strip():
        entry["_preview"] = preview.strip()
    return entry


def middleware_cache_deserialize(entry: str | dict[str, Any], correlation_id: int) -> Any:
    """Rebuild ``EngineIoReturn`` from middleware cache storage."""
    from mas.runtime.schema.ingress import EngineIoReturn, ToolCallSpec

    if isinstance(entry, str):
        return EngineIoReturn(
            correlation_id=correlation_id,
            response_kind="MODEL_TEXT",
            next_step="STOP",
            text=entry,
        )
    if not isinstance(entry, dict):
        raise ValueError(f"invalid llm_cache entry type: {type(entry).__name__}")

    next_step = str(entry.get("next_step") or "STOP")
    parallel_raw = entry.get("parallel_tools") or []
    parallel_tools = tuple(
        ToolCallSpec(
            tool_name=str(row.get("tool_name") or ""),
            tool_arguments=dict(row.get("tool_arguments") or {}),
        )
        for row in parallel_raw
        if isinstance(row, dict) and str(row.get("tool_name") or "").strip()
    )
    text = str(entry.get("text") or entry.get("content") or "")
    tool_name = str(entry.get("tool_name") or "")
    if not tool_name and entry.get("tool_calls"):
        tool_calls = entry.get("tool_calls") or []
        if isinstance(tool_calls, list) and tool_calls:
            fn = (tool_calls[0].get("function") or {}) if isinstance(tool_calls[0], dict) else {}
            tool_name = str(fn.get("name") or "")
            raw_args = fn.get("arguments") or "{}"
            try:
                tool_arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                tool_arguments = {"raw": raw_args}
            return EngineIoReturn(
                correlation_id=correlation_id,
                response_kind="MODEL_TEXT",
                next_step="TOOL_CALL",
                tool_name=tool_name,
                tool_arguments=tool_arguments if isinstance(tool_arguments, dict) else {},
                text="",
                usage=dict(entry.get("usage") or {}),
                finish_reason=str(entry.get("finish_reason") or "tool_calls"),
            )

    return EngineIoReturn(
        correlation_id=correlation_id,
        response_kind="MODEL_TEXT",
        next_step=next_step,
        text=text,
        tool_name=tool_name,
        tool_arguments=dict(entry.get("tool_arguments") or {}),
        parallel_tools=parallel_tools,
        usage=dict(entry.get("usage") or {}),
        finish_reason=str(entry.get("finish_reason") or ""),
    )


def assistant_message_from_cache_content(content: str | None) -> dict[str, Any] | None:
    """Parse a cache entry into an OpenAI-shaped assistant message."""
    if not content or not str(content).strip():
        return None
    text = str(content)
    if not text.startswith("{"):
        return {"role": "assistant", "content": text}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"role": "assistant", "content": text}
    if isinstance(parsed, dict) and parsed.get("tool_calls"):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": parsed["tool_calls"],
        }
    return {"role": "assistant", "content": text}
