#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Anthropic Messages API as a protocol layer over an http transport.

Speaks ``POST {api_base}/messages``. Posts through ``inner.aclient``.
``achat_completion`` is the OpenAI-shaped adapter.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from mas.library.standard.plugins.llm.http_protocol import (
    HttpLlmProtocol,
    araise_for_status,
    cache_breakpoints,
    cache_ttl,
    normalize_usage,
    sampling_value,
)
from mas.library.standard.plugins.llm.structured import schema_from_format
from mas.library.standard.plugins.llm.thinking import (
    AnthropicThinking,
    anthropic_thinking,
    loop_replayable,
    thinking_off,
)
from mas.runtime.engine.llm_reasoning import ReasoningSettings, sanitize_assistant_message
from mas.runtime.engine.llm_request import apply_sampling_payload, merge_extra_body, sampling_settings_from_entry
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.reliability.classify import provider_failure
from mas.runtime.secrets.ref import SecretRef

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_THINKING_KEY = "anthropic_thinking_blocks"
_DEFAULT_MAX_TOKENS = 8192
_STOP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "stop_sequence": "stop",
    "refusal": "content_filter",
}


class AnthropicLLMProvider(HttpLlmProtocol):
    """Anthropic Messages layer. ``protocol: anthropic`` plus a url is ``[http, anthropic]``."""

    provider_id = "anthropic"
    kind = "anthropic"
    default_api_base = "https://api.anthropic.com/v1"
    default_secret = SecretRef(name="anthropic")

    def _auth_header(self) -> tuple[str, str]:
        return "x-api-key", ""

    def _build_chat_request(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
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
        url = self.api_base.rstrip("/") + "/messages"
        cap = max_completion_tokens if max_completion_tokens is not None else max_tokens
        limit = int(cap) if cap is not None else _DEFAULT_MAX_TOKENS
        thinking = anthropic_thinking(model, reasoning, max_tokens=limit)
        if thinking.active and not loop_replayable(messages, _THINKING_KEY):
            thinking = AnthropicThinking(effort=thinking.effort, always_on=thinking.always_on)
        system, mapped = _messages(messages, replay=thinking.always_on or not thinking_off(reasoning))
        payload: dict[str, Any] = {"model": model, "messages": mapped, "max_tokens": limit}
        if system:
            payload["system"] = system
        if thinking.thinking:
            payload["thinking"] = thinking.thinking
        if not thinking.constrains:
            for name in ("temperature", "top_p", "top_k"):
                value = sampling_value(sampling, model, name)
                if value is not None:
                    payload[name] = value
        if sampling:
            sampled = apply_sampling_payload({}, sampling_settings_from_entry(sampling, model=model), model=model)
            if sampled.get("stop"):
                payload["stop_sequences"] = (
                    list(sampled["stop"]) if not isinstance(sampled["stop"], str) else [sampled["stop"]]
                )
            fmt = sampled.get("response_format", response_format)
        else:
            fmt = response_format
        extra = merge_extra_body(getattr(self, "extra_body", None), extra_body)
        if extra:
            payload.update(extra)
        payload.pop("response_format", None)
        mapped_tools = _tools(tools)
        if mapped_tools:
            payload["tools"] = mapped_tools
        choice = _tool_choice(tool_choice) if mapped_tools else None
        if choice and not (thinking.constrains and choice["type"] in {"any", "tool"}):
            payload["tool_choice"] = choice
        schema = schema_from_format(fmt)
        config = dict(payload["output_config"]) if isinstance(payload.get("output_config"), dict) else {}
        if schema:
            config["format"] = {"type": "json_schema", "schema": schema}
        if thinking.effort:
            config["effort"] = thinking.effort
        if config:
            payload["output_config"] = config
        _mark_cacheable(payload, cache_breakpoints(sampling, model), cache_ttl(sampling, model))
        headers = {
            **self._key_headers(),
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        query = {str(k): v for k, v in extra_query.items()} if extra_query else None
        return url, payload, headers, query

    def _message_from_completion(self, data: dict[str, Any], reasoning: ReasoningSettings, **_: Any) -> dict[str, Any]:
        blocks = data.get("content") if isinstance(data.get("content"), list) else []
        texts: list[str] = []
        thoughts: list[str] = []
        signed: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            if kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind in {"thinking", "redacted_thinking"}:
                signed.append(dict(block))
                if kind == "thinking" and block.get("thinking"):
                    thoughts.append(str(block["thinking"]))
            elif kind == "tool_use":
                tool_calls.append(
                    {
                        "id": str(block.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": json.dumps(block.get("input") or {}, default=str),
                        },
                    }
                )
        message: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
        if thoughts:
            message["reasoning"] = "\n".join(thoughts)
        if tool_calls:
            message["tool_calls"] = tool_calls
            if signed:
                message["provider_state"] = {_THINKING_KEY: signed}
        usage = _with_total(_usage(data.get("usage")))
        if usage:
            message["usage"] = usage
        finish = _STOP.get(str(data.get("stop_reason") or ""), str(data.get("stop_reason") or "") or None)
        if finish:
            message["finish_reason"] = finish
        _check_complete(message, data.get("stop_reason"), data.get("stop_details"))
        return sanitize_assistant_message(message, exclude=reasoning.exclude)

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
        acc = _StreamAcc()
        try:
            async with self._get_async_client().stream(
                "POST", url, json=stream_payload, headers=headers, params=params
            ) as resp:
                await araise_for_status(resp)
                async for raw_line in resp.aiter_lines():
                    acc.feed(raw_line, on_stream_chunk)
        except ClassifiedFailure:
            raise
        except Exception as exc:
            await self._areraise_stream_failure(exc, emitted_any=acc.emitted_any)
        return acc.message(reasoning)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {None, "text"}
        ]
        return "".join(parts)
    return "" if content is None else str(content)


def _messages(messages: list[dict[str, Any]], *, replay: bool = True) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_tools: list[dict[str, Any]] = []

    def flush_tools() -> None:
        if not pending_tools:
            return
        out.append({"role": "user", "content": list(pending_tools)})
        pending_tools.clear()

    for raw in messages:
        role = str(raw.get("role") or "")
        if role == "system":
            text = _text_of(raw.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": str(raw.get("tool_call_id") or raw.get("id") or ""),
                "content": _text_of(raw.get("content")),
            }
            if raw.get("is_error"):
                result["is_error"] = True
                result["content"] = str(result["content"]).strip() or "(no output)"
            pending_tools.append(result)
            continue
        flush_tools()
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            state = raw.get("provider_state")
            if replay and isinstance(state, dict) and isinstance(state.get(_THINKING_KEY), list):
                blocks.extend(dict(b) for b in state[_THINKING_KEY] if isinstance(b, dict))
            text = _text_of(raw.get("content"))
            if text.strip():
                blocks.append({"type": "text", "text": text})
            for call in raw.get("tool_calls") or []:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "input": args,
                    }
                )
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue
        text = _text_of(raw.get("content"))
        if text.strip():
            out.append({"role": "user", "content": text})
    flush_tools()
    return "\n".join(system_parts), out


def _mark_cacheable(payload: dict[str, Any], places: tuple[bool, bool, int], ttl: str | None) -> None:
    """Prompt-cache breakpoints on the system prompt, the last tool and the last user turns (the API allows four)."""
    on_system, on_tools, on_messages = places
    mark: dict[str, Any] = {"type": "ephemeral"}
    if ttl:
        mark["ttl"] = ttl
    system = payload.get("system")
    if on_system and isinstance(system, str) and system:
        payload["system"] = [{"type": "text", "text": system, "cache_control": dict(mark)}]
    tools = payload.get("tools")
    if on_tools and isinstance(tools, list) and tools:
        tools[-1]["cache_control"] = dict(mark)
    marked = 0
    for message in reversed(payload.get("messages") or []):
        if marked >= on_messages:
            break
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
            message["content"] = content
        if isinstance(content, list) and content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = dict(mark)
            marked += 1


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
        mapped.append({"name": name, "description": str(fn.get("description") or ""), "input_schema": schema})
    return mapped


def _tool_choice(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice in (None, "auto"):
        return None
    if tool_choice == "none":
        return {"type": "none"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else tool_choice
        name = str(fn.get("name") or "").strip()
        if name:
            return {"type": "tool", "name": name}
    return None


def _usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    prompt = raw.get("input_tokens")
    completion = raw.get("output_tokens")
    out = normalize_usage(raw)
    if prompt is not None:
        cached = int(raw.get("cache_creation_input_tokens") or 0) + int(raw.get("cache_read_input_tokens") or 0)
        out["prompt_tokens"] = int(prompt) + cached
    if completion is not None:
        out["completion_tokens"] = int(completion)
    return out


def _with_total(usage: dict[str, Any]) -> dict[str, Any]:
    if "prompt_tokens" in usage or "completion_tokens" in usage:
        usage["total_tokens"] = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    return usage


class _StreamAcc:
    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.usage: dict[str, Any] = {}
        self.finish_reason = ""
        self.stop_reason = ""
        self.stop_details: Any = None
        self.emitted_any = False
        self._json_idx: dict[int, str] = {}
        self.signed: dict[int, dict[str, Any]] = {}

    def feed(self, raw_line: Any, on_stream_chunk: Callable[[str], None] | None) -> None:
        event = _sse_event(raw_line)
        if not event:
            return
        kind = str(event.get("type") or "")
        if kind == "error":
            raise _stream_error(event.get("error"))
        if kind == "message_start":
            usage = _usage((event.get("message") or {}).get("usage"))
            if usage:
                self.usage.update(usage)
            return
        if kind == "content_block_start":
            idx = int(event.get("index") or 0)
            block = event.get("content_block") or {}
            if block.get("type") == "thinking":
                self.signed[idx] = {
                    "type": "thinking",
                    "thinking": str(block.get("thinking") or ""),
                    "signature": str(block.get("signature") or ""),
                }
            elif block.get("type") == "redacted_thinking":
                self.signed[idx] = {"type": "redacted_thinking", "data": str(block.get("data") or "")}
            if block.get("type") == "tool_use":
                self.tool_calls[idx] = {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(block.get("name") or ""), "arguments": ""},
                }
                self._json_idx[idx] = ""
            return
        if kind == "content_block_delta":
            idx = int(event.get("index") or 0)
            delta = event.get("delta") or {}
            dtype = str(delta.get("type") or "")
            if dtype == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    self.content_parts.append(text)
                    self.emitted_any = True
                    if callable(on_stream_chunk):
                        try:
                            on_stream_chunk(text)
                        except Exception:
                            logger.exception("on_stream_chunk callback failed")
            elif dtype == "thinking_delta" and idx in self.signed:
                self.signed[idx]["thinking"] += str(delta.get("thinking") or "")
            elif dtype == "signature_delta" and idx in self.signed:
                self.signed[idx]["signature"] += str(delta.get("signature") or "")
            elif dtype == "input_json_delta":
                self.emitted_any = True
                self._json_idx[idx] = self._json_idx.get(idx, "") + str(delta.get("partial_json") or "")
                slot = self.tool_calls.get(idx)
                if slot:
                    slot["function"]["arguments"] = self._json_idx[idx]
            return
        if kind == "message_delta":
            delta = event.get("delta") or {}
            stop = delta.get("stop_reason")
            if stop:
                self.stop_reason = str(stop)
                self.finish_reason = _STOP.get(str(stop), str(stop))
            if delta.get("stop_details"):
                self.stop_details = delta["stop_details"]
            usage = _usage(event.get("usage"))
            if usage:
                self.usage.update(usage)

    def message(self, reasoning: ReasoningSettings) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content_parts) or None}
        thoughts = [b["thinking"] for _, b in sorted(self.signed.items()) if b.get("thinking")]
        if thoughts:
            message["reasoning"] = "\n".join(thoughts)
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
            if self.signed:
                message["provider_state"] = {_THINKING_KEY: [b for _, b in sorted(self.signed.items())]}
        if self.usage:
            message["usage"] = _with_total(dict(self.usage))
        if self.finish_reason:
            message["finish_reason"] = self.finish_reason
        _check_complete(message, self.stop_reason, self.stop_details)
        return sanitize_assistant_message(message, exclude=reasoning.exclude)


def _check_complete(message: dict[str, Any], stop_reason: Any, stop_details: Any = None) -> None:
    """A refusal or a tool call cut off by ``max_tokens`` is a failure, not a quiet empty turn."""
    if str(stop_reason or "") == "refusal":
        details = stop_details if isinstance(stop_details, dict) else {}
        category = str(details.get("category") or "")
        raise ClassifiedFailure(
            f"Anthropic refused the request{f' ({category})' if category else ''}: "
            f"{details.get('explanation') or 'no details provided'}",
            failure_class=FailureClass.APPLICATION,
            code="REFUSAL",
        )
    if str(stop_reason or "") == "max_tokens":
        for call in message.get("tool_calls") or []:
            try:
                json.loads(call["function"]["arguments"] or "{}")
            except (TypeError, ValueError):
                raise ClassifiedFailure(
                    "The response hit max_tokens while writing a tool call; raise max_tokens or split the task.",
                    failure_class=FailureClass.APPLICATION,
                    code="TRUNCATED_TOOL_CALL",
                ) from None


def _stream_error(raw: Any) -> ClassifiedFailure:
    """An in-stream ``error`` event: the HTTP status was 200, the answer is not usable."""
    return provider_failure("Anthropic stream", error=raw)


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
