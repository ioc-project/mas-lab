#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Open Responses as a protocol layer over an http transport.

Speaks ``POST {api_base}/responses`` (https://www.openresponses.org). The history goes out as
typed items (``message``, ``function_call``, ``function_call_output``, ``reasoning``) and the
answer comes back as output items. ``achat_completion`` is the OpenAI-shaped adapter on top.

The layer is stateless: it sends ``store: false`` and the whole history every call, so a
reasoning item's ``encrypted_content`` is carried in ``provider_state`` and replayed with its
tool call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from mas.library.standard.plugins.llm.http_protocol import (
    HttpLlmProtocol,
    araise_for_status,
    catalog_supports,
    normalize_usage,
    sampling_value,
)
from mas.library.standard.plugins.llm.structured import apply_structured_result, schema_from_format
from mas.library.standard.plugins.llm.thinking import thinking_off, wants_thinking
from mas.runtime.engine.llm_reasoning import ReasoningSettings, sanitize_assistant_message
from mas.runtime.engine.llm_request import apply_sampling_payload, merge_extra_body, sampling_settings_from_entry
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

logger = logging.getLogger(__name__)

_STATE_KEY = "openresponses_reasoning_items"
_ENCRYPTED = "reasoning.encrypted_content"
_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
_TERMINAL = {"response.completed", "response.incomplete", "response.failed"}


class OpenResponsesLLMProvider(HttpLlmProtocol):
    """Open Responses layer. ``protocol: openresponses`` plus a url is ``[http, openresponses]``."""

    provider_id = "openresponses"
    kind = "openresponses"
    default_api_base = "https://api.openai.com/v1"
    default_api_key_env = "OPENAI_API_KEY"

    def _build_chat_request(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        api_key: str,
        reasoning: ReasoningSettings,
        max_completion_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
        extra_query: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        tool_choice: Any = None,
        response_format: Any = None,
        **_: Any,
    ) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        del temperature  # the call's default 0.7 is not a choice; only `sampling` is sent
        url = self.api_base.rstrip("/") + "/responses"
        instructions, items = _input_items(messages, replay=not thinking_off(reasoning))
        payload: dict[str, Any] = {"model": model, "input": items, "store": False}
        if instructions:
            payload["instructions"] = instructions
        cap = max_completion_tokens if max_completion_tokens is not None else max_tokens
        if cap is not None:
            payload["max_output_tokens"] = int(cap)
        for name in ("temperature", "top_p"):
            value = sampling_value(sampling, model, name)
            if value is not None:
                payload[name] = value
        fmt = response_format
        if sampling:
            sampled = apply_sampling_payload({}, sampling_settings_from_entry(sampling, model=model), model=model)
            fmt = sampled.get("response_format", response_format)
            for name in ("prompt_cache_key", "prompt_cache_retention"):
                if sampling.get(name) is not None and catalog_supports(model, name):
                    payload[name] = sampling[name]
        if "prompt_cache_retention" in payload:
            payload["prompt_cache_retention"] = _retention(payload["prompt_cache_retention"])
        settings = _reasoning(reasoning)
        if settings:
            payload["reasoning"] = settings
        include = list(reasoning.include or ())
        if wants_thinking(reasoning) and _ENCRYPTED not in include:
            include.append(_ENCRYPTED)
        if include:
            payload["include"] = include
        schema = schema_from_format(fmt)
        if schema:
            payload["text"] = {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}
        mapped_tools = _tools(tools)
        if mapped_tools:
            payload["tools"] = mapped_tools
        choice = _tool_choice(tool_choice) if mapped_tools else None
        if choice:
            payload["tool_choice"] = choice
        extra = merge_extra_body(getattr(self, "extra_body", None), extra_body)
        if extra:
            payload.update(extra)
        payload.pop("response_format", None)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        query = {str(k): v for k, v in extra_query.items()} if extra_query else None
        return url, payload, headers, query

    def _structured_message(self, message: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        text = payload.get("text")
        fmt = text.get("format") if isinstance(text, dict) else None
        schema = fmt.get("schema") if isinstance(fmt, dict) and isinstance(fmt.get("schema"), dict) else None
        return apply_structured_result(message, schema)

    def _message_from_completion(self, data: dict[str, Any], reasoning: ReasoningSettings, **_: Any) -> dict[str, Any]:
        return _message(data, reasoning)

    async def _achat_completion_streamed(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        on_stream_chunk: Callable[[str], None] | None,
        reasoning: ReasoningSettings,
        params: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        final: dict[str, Any] | None = None
        done_items: list[dict[str, Any]] = []
        emitted_any = False
        try:
            async with self._get_async_client().stream(
                "POST", url, json=stream_payload, headers=headers, params=params
            ) as resp:
                await araise_for_status(resp)
                async for raw_line in resp.aiter_lines():
                    event = _sse_event(raw_line)
                    if not event:
                        continue
                    kind = str(event.get("type") or "")
                    if kind == "error":
                        raise _stream_error(event)
                    if kind == "response.output_text.delta":
                        text = str(event.get("delta") or "")
                        if text:
                            emitted_any = True
                            if callable(on_stream_chunk):
                                try:
                                    on_stream_chunk(text)
                                except Exception:
                                    logger.exception("on_stream_chunk callback failed")
                    elif kind == "response.function_call_arguments.delta":
                        emitted_any = True
                    elif kind == "response.output_item.done" and isinstance(event.get("item"), dict):
                        done_items.append(event["item"])
                    elif kind in _TERMINAL and isinstance(event.get("response"), dict):
                        final = event["response"]
                        if kind == "response.failed" and not final.get("status"):
                            final = {**final, "status": "failed"}
        except ClassifiedFailure:
            raise
        except Exception as exc:
            await self._areraise_stream_failure(exc, emitted_any=emitted_any)
        if final is None:
            final = {"status": "completed", "output": done_items}
        elif not final.get("output") and done_items:
            final = {**final, "output": done_items}
        return _message(final, reasoning)


def _retention(value: Any) -> str:
    """``in_memory`` or ``24h`` (``5m`` is the first, ``1h`` the second)."""
    return "in_memory" if str(value) in {"in_memory", "5m"} else "24h"


def _reasoning(reasoning: ReasoningSettings) -> dict[str, Any]:
    """The Open Responses ``reasoning`` object: effort, mode, and a summary when the spec keeps the thoughts."""
    out: dict[str, Any] = {}
    effort = str(reasoning.effort or "").lower()
    if wants_thinking(reasoning) and effort in _EFFORTS:
        out["effort"] = effort
    elif thinking_off(reasoning) and effort == "none":
        out["effort"] = "none"
    if reasoning.mode:
        out["mode"] = reasoning.mode
    if out and not reasoning.exclude:
        out["summary"] = "auto"
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {None, "text", "input_text", "output_text"}
        )
    return "" if content is None else str(content)


def _input_items(messages: list[dict[str, Any]], *, replay: bool = True) -> tuple[str, list[dict[str, Any]]]:
    """``(instructions, input items)`` for the history: system turns become ``instructions``."""
    system_parts: list[str] = []
    items: list[dict[str, Any]] = []
    for raw in messages:
        role = str(raw.get("role") or "")
        text = _text_of(raw.get("content"))
        if role in {"system", "developer"}:
            if text:
                system_parts.append(text)
        elif role == "tool":
            output = text if text.strip() else "(no output)"
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(raw.get("tool_call_id") or raw.get("id") or ""),
                    "output": output,
                }
            )
        elif role == "assistant":
            state = raw.get("provider_state")
            if replay and isinstance(state, dict) and isinstance(state.get(_STATE_KEY), list):
                items.extend(dict(i) for i in state[_STATE_KEY] if isinstance(i, dict))
            if text.strip():
                items.append(
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )
            for call in raw.get("tool_calls") or []:
                fn = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments": str(fn.get("arguments") or "{}"),
                    }
                )
        elif text.strip():
            items.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
    return "\n".join(system_parts), items


def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        schema = (
            fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}}
        )
        mapped.append(
            {"type": "function", "name": name, "description": str(fn.get("description") or ""), "parameters": schema}
        )
    return mapped


def _tool_choice(tool_choice: Any) -> Any:
    if tool_choice in (None, "auto"):
        return None
    if tool_choice in ("none", "required"):
        return tool_choice
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else tool_choice
        name = str(fn.get("name") or "").strip()
        if name:
            return {"type": "function", "name": name}
    return None


def _usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out = normalize_usage(raw)
    if raw.get("input_tokens") is not None:
        out["prompt_tokens"] = int(raw["input_tokens"])
    if raw.get("output_tokens") is not None:
        out["completion_tokens"] = int(raw["output_tokens"])
    if "prompt_tokens" in out or "completion_tokens" in out:
        out["total_tokens"] = int(raw.get("total_tokens") or 0) or int(out.get("prompt_tokens") or 0) + int(
            out.get("completion_tokens") or 0
        )
    return out


def _message(data: dict[str, Any], reasoning: ReasoningSettings) -> dict[str, Any]:
    """The OpenAI-shaped assistant message for a response object."""
    status = str(data.get("status") or "completed")
    if status == "failed":
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        raise _response_error(err)
    texts: list[str] = []
    refusals: list[str] = []
    thoughts: list[str] = []
    kept: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text":
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "refusal":
                    refusals.append(str(part.get("refusal") or ""))
        elif kind == "reasoning":
            if item.get("encrypted_content"):
                kept.append({k: v for k, v in item.items() if k in {"type", "id", "summary", "encrypted_content"}})
            for part in item.get("summary") or []:
                if isinstance(part, dict) and part.get("text"):
                    thoughts.append(str(part["text"]))
        elif kind == "function_call":
            tool_calls.append(
                {
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(item.get("name") or ""), "arguments": str(item.get("arguments") or "{}")},
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
    if thoughts:
        message["reasoning"] = "\n".join(thoughts)
    if tool_calls:
        message["tool_calls"] = tool_calls
        if kept:
            message["provider_state"] = {_STATE_KEY: kept}
    usage = _usage(data.get("usage"))
    if usage:
        message["usage"] = usage
    incomplete = data.get("incomplete_details") if isinstance(data.get("incomplete_details"), dict) else {}
    reason = str(incomplete.get("reason") or "")
    if status == "incomplete":
        message["finish_reason"] = "content_filter" if reason == "content_filter" else "length"
    elif tool_calls:
        message["finish_reason"] = "tool_calls"
    else:
        message["finish_reason"] = "stop"
    if refusals and not texts and not tool_calls:
        raise ClassifiedFailure(
            f"The model refused the request: {' '.join(refusals)}",
            failure_class=FailureClass.APPLICATION,
            code="REFUSAL",
        )
    if status == "incomplete":
        for call in tool_calls:
            try:
                json.loads(call["function"]["arguments"] or "{}")
            except (TypeError, ValueError):
                raise ClassifiedFailure(
                    "The response hit max_output_tokens while writing a tool call; raise max_tokens or split the task.",
                    failure_class=FailureClass.APPLICATION,
                    code="TRUNCATED_TOOL_CALL",
                ) from None
    return sanitize_assistant_message(message, exclude=reasoning.exclude)


_TRANSIENT_CODES = frozenset({"server_error", "rate_limit_exceeded", "overloaded", "timeout"})


def _response_error(err: dict[str, Any]) -> ClassifiedFailure:
    code = str(err.get("code") or "response_failed")
    return ClassifiedFailure(
        f"Open Responses call failed: {code}: {err.get('message') or 'no detail'}",
        failure_class=FailureClass.TRANSIENT if code in _TRANSIENT_CODES else FailureClass.APPLICATION,
        code=code,
    )


def _stream_error(event: dict[str, Any]) -> ClassifiedFailure:
    """An in-stream ``error`` event: the HTTP status was 200, the answer is not usable."""
    err = event.get("error") if isinstance(event.get("error"), dict) else event
    return _response_error({"code": err.get("code"), "message": err.get("message")})


def _sse_event(raw_line: Any) -> dict[str, Any] | None:
    line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
    if not line.startswith("data:"):
        return None
    data_str = line[len("data:") :].strip()
    if not data_str or data_str == "[DONE]":
        return None
    try:
        event = json.loads(data_str)
    except (TypeError, ValueError):
        return None
    return event if isinstance(event, dict) else None
