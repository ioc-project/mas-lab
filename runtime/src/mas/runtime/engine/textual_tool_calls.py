#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Fallback recovery for models that emit tool calls inside message content.

Some local OpenAI-compatible backends return tool calls as plain assistant text
(`<|tool_call>call:fn(args)<tool_call|>`, bare `name(args)`, multiline JSON, ...)
instead of the structured ``tool_calls`` field. This module is opt-in recovery
used by ``LiveLlmEngine`` only when structured tool calls are absent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

_TEXT_TOOL_CALL_RE = re.compile(
    r"^\s*call:(?P<name>[A-Za-z0-9_.-]+)\((?P<args>.*)\)\s*$",
    re.DOTALL,
)
_BARE_TOOL_CALL_RE = re.compile(r"^\s*(?:-\s*)?(?P<name>[A-Za-z0-9_.-]+)\((?P<args>.*)\)\s*$")
_BARE_CALL_HEAD_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\(")
_TEXT_TOOL_CALL_BLOCK_RE = re.compile(r"<\|tool_call>(.*?)<tool_call\|>", re.DOTALL)
_CHANNEL_MARKUP_RE = re.compile(r"<\|channel>[^\n<]*\n?(?:<channel\|>)?", re.DOTALL)
_TEXT_VAR_RE = re.compile(r"^\s*(?:-\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.+?)\s*$")
_MERGED_ARG_KEY_RE = re.compile(
    r"^(?P<first_key>[A-Za-z_][A-Za-z0-9_]*)=(?P<first_value>.*),(?P<second_key>[A-Za-z_][A-Za-z0-9_]*)$",
    re.DOTALL,
)


@dataclass(frozen=True)
class _ParamSchema:
    """Per-tool parameter shape used to resolve bare positional arguments."""

    order: tuple[str, ...] = ()
    enums: dict[str, frozenset[Any]] = field(default_factory=dict)


def _build_tool_schemas(tool_defs: list[dict[str, Any]]) -> dict[str, _ParamSchema]:
    """Extract declared parameter order + enum constraints per tool name.

    Used only to resolve bare positional arguments (see
    ``_resolve_positional_args``) -- never to validate or reject a call.
    """
    schemas: dict[str, _ParamSchema] = {}
    for tool_def in tool_defs:
        if not isinstance(tool_def, dict):
            continue
        fn = tool_def.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        params = fn.get("parameters")
        if not isinstance(name, str) or not isinstance(params, dict):
            continue
        properties = params.get("properties")
        if not isinstance(properties, dict):
            continue
        enums: dict[str, frozenset[Any]] = {}
        for prop_name, prop_schema in properties.items():
            if isinstance(prop_schema, dict) and isinstance(prop_schema.get("enum"), list):
                enums[prop_name] = frozenset(prop_schema["enum"])
        schemas[name] = _ParamSchema(order=tuple(properties.keys()), enums=enums)
    return schemas


def repair_merged_arg_keys(args: dict[str, Any]) -> dict[str, Any]:
    """Undo a malformed structured tool-call arguments dict from some local models.

    For array-typed parameters (e.g. run_skill_script's ``args``) a backend may
    emit valid-but-wrong JSON: ``{"args=[...]" : "runner.py", ...}`` instead of
    ``{"args": [...], "script": "runner.py", ...}`` -- the array literal ends up
    as text glued onto the START of the key that should have followed it.
    ``json.loads`` succeeds (it is syntactically valid JSON), so this must be
    repaired after parsing, not caught as a decode error.
    """
    repaired: dict[str, Any] = {}
    for key, value in args.items():
        match = _MERGED_ARG_KEY_RE.match(key)
        if not match:
            repaired[key] = value
            continue
        try:
            first_value: Any = json.loads(match.group("first_value"))
        except json.JSONDecodeError:
            first_value = match.group("first_value")
        repaired[match.group("first_key")] = first_value
        repaired[match.group("second_key")] = value
    return repaired


def strip_channel_markup(text: str | None) -> str:
    return _CHANNEL_MARKUP_RE.sub("", str(text or "")).strip()


def _args_from_xml_tool_body(inner: str) -> dict[str, Any]:
    stripped = inner.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            loaded = json.loads(stripped)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
    return {"task": stripped}


def _xml_named_tool_calls(content: str, known_names: set[str]) -> tuple[list[dict[str, Any]], str]:
    """Recover ``<tool_name>body</tool_name>`` blocks some backends emit as prose."""
    if not content or not known_names:
        return [], content
    by_lower = {name.lower(): name for name in known_names}
    found: list[tuple[int, int, str, str]] = []
    for name in known_names:
        pattern = re.compile(
            rf"<{re.escape(name)}>\s*(.*?)\s*</{re.escape(name)}>",
            re.DOTALL | re.IGNORECASE,
        )
        for match in pattern.finditer(content):
            canonical = by_lower.get(match.group(0)[1:].split(">", 1)[0].lower(), name)
            found.append((match.start(), match.end(), canonical, match.group(1).strip()))
    found.sort()
    used: list[tuple[int, int]] = []
    tool_calls: list[dict[str, Any]] = []
    for start, end, name, inner in found:
        if any(start < prev_end and end > prev_start for prev_start, prev_end in used):
            continue
        used.append((start, end))
        tool_calls.append(_as_tool_call(len(tool_calls) + 1, name, _args_from_xml_tool_body(inner)))
    cleaned = content
    for start, end in sorted(used, reverse=True):
        cleaned = (cleaned[:start] + cleaned[end:]).strip()
    return tool_calls, cleaned


def recover_tool_calls_from_content(
    content: Any,
    *,
    known_tool_names: set[str] | None = None,
    tool_schemas: dict[str, _ParamSchema] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Recover structured tool calls from message content, plus the leftover text."""
    from mas.runtime.contracts.content_types import text_of

    raw = text_of(content)
    cleaned = strip_channel_markup(raw)
    blocks = _TEXT_TOOL_CALL_BLOCK_RE.findall(raw)
    variables = _collect_text_variables(cleaned)
    tool_schemas = tool_schemas or {}
    tool_calls: list[dict[str, Any]] = []

    for idx, item in enumerate(blocks or ([raw] if cleaned.startswith("call:") else []), start=1):
        match = _TEXT_TOOL_CALL_RE.match(item.strip())
        if match:
            name = match.group("name")
            args = _parse_textual_tool_call_args(
                match.group("args"), variables=variables, param_schema=tool_schemas.get(name)
            )
            tool_calls.append(_as_tool_call(idx, name, args))

    if not tool_calls:
        for line in cleaned.splitlines():
            match = _BARE_TOOL_CALL_RE.match(line.strip())
            if match:
                name = match.group("name")
                args = _parse_textual_tool_call_args(
                    match.group("args"), variables=variables, param_schema=tool_schemas.get(name)
                )
                tool_calls.append(_as_tool_call(len(tool_calls) + 1, name, args))

    if not tool_calls and known_tool_names:
        spans: list[tuple[int, int]] = []
        for name, args_raw, start, end in _find_multiline_bare_calls(cleaned, known_names=known_tool_names):
            args = _parse_textual_tool_call_args(args_raw, variables=variables, param_schema=tool_schemas.get(name))
            tool_calls.append(_as_tool_call(len(tool_calls) + 1, name, args))
            spans.append((start, end))
        for start, end in sorted(spans, reverse=True):
            cleaned = (cleaned[:start] + cleaned[end:]).strip()

    if not tool_calls and known_tool_names:
        xml_calls, cleaned = _xml_named_tool_calls(cleaned, known_tool_names)
        tool_calls.extend(xml_calls)

    if blocks:
        cleaned = _TEXT_TOOL_CALL_BLOCK_RE.sub("", cleaned).strip()
    if tool_calls and cleaned.startswith("call:"):
        cleaned = ""
    return tool_calls, cleaned


def maybe_recover_textual_tool_calls(
    message: dict[str, Any],
    tool_defs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply textual tool-call recovery when the assistant message has no structured calls."""
    if message.get("tool_calls"):
        return message

    known_tool_names = {
        str(fn.get("name"))
        for t in tool_defs
        if isinstance(t, dict) and isinstance(fn := t.get("function"), dict) and fn.get("name")
    }
    tool_calls, recovered_text = recover_tool_calls_from_content(
        message.get("content"),
        known_tool_names=known_tool_names,
        tool_schemas=_build_tool_schemas(tool_defs),
    )
    # Strip channel markup even when no tool call was recovered -- some backends
    # emit it on plain answers too, and it must never reach the user.
    return {**message, "tool_calls": tool_calls, "content": recovered_text}


def _parse_textual_tool_call_args(
    raw: str,
    *,
    variables: dict[str, Any] | None = None,
    param_schema: _ParamSchema | None = None,
) -> dict[str, Any]:
    variables = variables or {}
    stripped = raw.strip()
    if stripped and "=" not in stripped and ":" not in stripped and stripped in variables:
        return {"value": variables[stripped]}

    payload = raw.replace('<|"|>', '"').strip()
    if not payload:
        return {}
    for name, value in variables.items():
        payload = re.sub(rf"\b{re.escape(name)}\b", json.dumps(value), payload)
    payload = payload.replace("=", ":")

    placeholder_order: list[str] = []
    if not payload.startswith("{"):
        payload, placeholder_order = _neutralize_bare_positional_args(payload)

    payload = re.sub(
        r"(^|[,{]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:",
        lambda m: f'{m.group(1)}"{m.group(2)}": ',
        payload,
    )
    if not payload.startswith("{"):
        payload = "{" + payload + "}"
    try:
        loaded = yaml.safe_load(payload)
    except Exception:
        return {"raw": raw}
    if not isinstance(loaded, dict):
        return {"raw": raw}
    if placeholder_order:
        return _resolve_positional_args(loaded, placeholder_order, param_schema)
    return dict(loaded)


def _split_top_level_args(payload: str) -> list[str]:
    """Split ``a, b: 1, "c, d"`` on top-level commas -- quotes/brackets aware."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    i = 0
    n = len(payload)
    while i < n:
        ch = payload[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(payload[start:i])
            start = i + 1
        i += 1
    parts.append(payload[start:])
    return [p.strip() for p in parts if p.strip()]


_KEYED_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:")


def _neutralize_bare_positional_args(payload: str) -> tuple[str, list[str]]:
    """Rewrite bare positional segments (e.g. ``blocking_queries``) as
    ``__posN__: blocking_queries`` placeholder key/value pairs.

    Without this, a comma-separated segment with no ``key:``/``key=`` prefix
    reaches ``yaml.safe_load`` wrapped in ``{...}``, and YAML's flow-mapping
    shorthand for a bare word silently turns it into ``{"blocking_queries":
    None}`` -- the argument's intended VALUE ends up as a phantom dict key
    mapped to null, instead of landing on the right parameter. The
    placeholders are resolved back onto real parameter names afterwards by
    ``_resolve_positional_args``.
    """
    segments = _split_top_level_args(payload)
    if not segments:
        return payload, []
    placeholder_order: list[str] = []
    rebuilt: list[str] = []
    for segment in segments:
        if _KEYED_SEGMENT_RE.match(segment):
            rebuilt.append(segment)
            continue
        placeholder = f"__pos{len(placeholder_order)}__"
        placeholder_order.append(placeholder)
        rebuilt.append(f"{placeholder}: {segment}")
    return ", ".join(rebuilt), placeholder_order


def _resolve_positional_args(
    loaded: dict[str, Any],
    placeholder_order: list[str],
    param_schema: _ParamSchema | None,
) -> dict[str, Any]:
    """Map ``__posN__`` placeholders onto real parameter names, in order.

    Prefers a parameter whose declared ``enum`` contains the value (so a
    bare ``blocking_queries`` lands on ``query_type``, not on whatever
    parameter happens to be first), then falls back to the next declared
    parameter not already supplied by a keyed argument in this same call.
    With no schema at all, falls back to the pre-existing ``value``/
    ``value1``/... convention used elsewhere in this module for a bare
    argument -- never back to the silent ``{token: None}`` shape.
    """
    resolved: dict[str, Any] = {k: v for k, v in loaded.items() if k not in placeholder_order}
    used = set(resolved)
    order = param_schema.order if param_schema else ()
    enums = param_schema.enums if param_schema else {}
    fallback = 0
    for placeholder in placeholder_order:
        if placeholder not in loaded:
            continue
        value = loaded[placeholder]
        target = next(
            (name for name, values in enums.items() if name not in used and value in values),
            None,
        )
        if target is None:
            target = next((name for name in order if name not in used), None)
        if target is None:
            target = "value" if fallback == 0 else f"value{fallback}"
            fallback += 1
        resolved[target] = value
        used.add(target)
    return resolved


def _collect_text_variables(content: str) -> dict[str, Any]:
    """``name_raw: <json>`` lines the model emits before referencing them by name."""
    variables: dict[str, Any] = {}
    for line in content.splitlines():
        match = _TEXT_VAR_RE.match(line)
        if not match or not match.group("name").endswith("_raw"):
            continue
        value_text = match.group("value").strip()
        try:
            variables[match.group("name")] = json.loads(value_text)
        except Exception:
            variables[match.group("name")] = value_text.strip('"')
    return variables


def _as_tool_call(index: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"text-tool-call-{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)},
    }


def _find_multiline_bare_calls(text: str, *, known_names: set[str]) -> list[tuple[str, str, int, int]]:
    """Find ``name(...)`` calls whose arguments span multiple lines.

    ``_BARE_TOOL_CALL_RE`` only matches a call that is a whole physical line,
    which misses models that write a tool call as pretty-printed JSON args across
    several lines, e.g. ``run_skill_script({\n  "skill": ...\n})``.
    Restricted to declared tool names so this never mistakes prose like
    "the discount (25%)" for a call.
    """
    calls: list[tuple[str, str, int, int]] = []
    for match in _BARE_CALL_HEAD_RE.finditer(text):
        name = match.group("name")
        if name not in known_names:
            continue
        depth = 1
        i = match.end()
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        if depth == 0:
            calls.append((name, text[match.end() : i - 1], match.start(), i))
    return calls
