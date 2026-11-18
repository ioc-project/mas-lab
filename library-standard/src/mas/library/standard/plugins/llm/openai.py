#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible chat-completions LLM provider.

Speaks ``POST {api_base}/chat/completions`` (OpenAI, Ollama, LiteLLM, …).
Named hosts subclass this layer.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from mas.library.standard.plugins.llm.http_protocol import HttpLlmProtocol, araise_for_status, normalize_usage
from mas.runtime.boundary.context.assemble import llm_tool_choice
from mas.runtime.engine.llm_reasoning import (
    ReasoningSettings,
    ReasoningStream,
    ThinkTagSplitter,
    apply_reasoning_payload,
    reasoning_text,
    sanitize_assistant_message,
    think_block_text,
)
from mas.runtime.engine.llm_request import (
    apply_output_token_limit,
    apply_sampling_payload,
    merge_extra_body,
    sampling_settings_from_entry,
)

logger = logging.getLogger(__name__)


_FOREIGN_MESSAGE_KEYS = ("provider_state", "is_error")


def _wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages without the keys other protocols read (the chat-completions wire rejects unknown properties)."""
    return [
        {k: v for k, v in m.items() if k not in _FOREIGN_MESSAGE_KEYS}
        if any(k in m for k in _FOREIGN_MESSAGE_KEYS)
        else m
        for m in messages
    ]


def _retention(value: Any) -> str:
    """``in_memory`` or ``24h``, the two values chat completions takes (``5m`` is the first, ``1h`` the second)."""
    return "in_memory" if str(value) in {"in_memory", "5m"} else "24h"


class OpenAILLMProvider(HttpLlmProtocol):
    """OpenAI-compatible chat-completions layer. ``achat_completion`` is the adapter on top."""

    provider_id = "openai"
    kind = "openai"
    default_api_base = "https://api.openai.com/v1"
    default_api_key_env = "OPENAI_API_KEY"
    # Hosts that reject unknown body fields turn this off; see ``OpenAICompatibleLLMProvider``.
    forwards_prompt_cache_fields = True

    async def _complete(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("reasoning_stream") is None:
            kwargs = dict(kwargs)
            kwargs["reasoning_stream"] = ReasoningStream(None)
        return await super()._complete(**kwargs)

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
        stream_options: dict[str, Any] | None = None,
        response_format: Any = None,
        **_: Any,
    ) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        url = self.api_base.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = apply_reasoning_payload(
            {"model": model, "messages": _wire_messages(messages)},
            reasoning,
            max_tokens=max_tokens,
        )
        nested_extra = payload.pop("extra_body", None)
        if not isinstance(nested_extra, dict):
            nested_extra = None
        if sampling:
            payload = apply_sampling_payload(payload, sampling_settings_from_entry(sampling, model=model), model=model)
        from mas.runtime.engine.llm_model_catalog import default_model_catalog

        info = default_model_catalog().get(model)
        # Explicit-cache markers have no chat-completions field; key and retention need a host that takes them.
        payload.pop("prompt_cache_breakpoints", None)
        for name in ("prompt_cache_key", "prompt_cache_retention"):
            if not self.forwards_prompt_cache_fields or (info is not None and not info.supports(name)):
                payload.pop(name, None)
        if "prompt_cache_retention" in payload:
            payload["prompt_cache_retention"] = _retention(payload["prompt_cache_retention"])
        payload["temperature"] = info.clamp("temperature", temperature) if info is not None else temperature
        extra = merge_extra_body(getattr(self, "extra_body", None), nested_extra, extra_body)
        if extra:
            payload.update(extra)
        payload = apply_output_token_limit(
            payload,
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens
            if max_completion_tokens is not None
            else (sampling or {}).get("max_completion_tokens"),
        )
        if stream_options:
            payload["stream_options"] = stream_options
        if response_format and "response_format" not in payload:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
            choice = tool_choice if tool_choice is not None else llm_tool_choice(messages, tools=tools)
            if choice:
                payload["tool_choice"] = choice
        elif tool_choice is not None:
            payload["tool_choice"] = tool_choice
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        query = {str(k): v for k, v in extra_query.items()} if extra_query else None
        return url, payload, headers, query

    @staticmethod
    def _message_from_completion(
        data: dict[str, Any],
        reasoning: ReasoningSettings,
        reasoning_stream: ReasoningStream | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        reasoning_stream = reasoning_stream or ReasoningStream(None)
        choices = data.get("choices") or []
        if not choices:
            return {}
        message = dict(choices[0].get("message") or {})
        reasoning_stream.received(reasoning_text(message) + think_block_text(message.get("content")))
        usage = normalize_usage(data.get("usage"))
        if usage:
            message["usage"] = usage
        finish_reason = choices[0].get("finish_reason")
        if finish_reason:
            message["finish_reason"] = finish_reason
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
        **extra: Any,
    ) -> dict[str, Any]:
        reasoning_stream = extra.get("reasoning_stream") or ReasoningStream(None)
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        content_parts: list[str] = []
        tool_call_accum: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        usage: dict[str, Any] = {}
        splitter = ThinkTagSplitter()
        emitted_any = False
        try:
            async with self._get_async_client().stream(
                "POST", url, json=stream_payload, headers=headers, params=params
            ) as resp:
                await araise_for_status(resp)
                async for raw_line in resp.aiter_lines():
                    line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        usage = normalize_usage(chunk["usage"])
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = fr
                    delta = choice.get("delta") or {}
                    answer = reasoning_stream.feed_delta(delta, splitter)
                    text = answer if reasoning.exclude else delta.get("content")
                    if text:
                        content_parts.append(text)
                        emitted_any = True
                        if callable(on_stream_chunk):
                            try:
                                on_stream_chunk(text)
                            except Exception:
                                logger.exception("on_stream_chunk callback failed")
                    for tc in delta.get("tool_calls") or []:
                        emitted_any = True
                        idx = int(tc.get("index", 0) or 0)
                        slot = tool_call_accum.setdefault(
                            idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        except Exception as exc:
            await self._areraise_stream_failure(exc, emitted_any=emitted_any)
        reasoning_stream.close()
        message: dict[str, Any] = {"content": "".join(content_parts) or None}
        if tool_call_accum:
            message["tool_calls"] = [tool_call_accum[i] for i in sorted(tool_call_accum)]
        if usage:
            message["usage"] = usage
        if finish_reason:
            message["finish_reason"] = finish_reason
        return sanitize_assistant_message(message, exclude=reasoning.exclude)
