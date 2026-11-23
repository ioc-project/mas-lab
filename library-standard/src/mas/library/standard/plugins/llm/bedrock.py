#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Amazon Bedrock Converse as a protocol layer over an http transport.

Speaks ``POST {api_base}/model/{modelId}/converse``. Bearer token
(``AWS_BEARER_TOKEN_BEDROCK``); SigV4 is not supported. Posts through
``inner.aclient``. ``stream: true`` calls ``converse`` and hands the whole text to
``on_stream_chunk`` at once: ``converse-stream`` is a binary event stream, not SSE.
A ``response_format`` is not sent to the model; the ``structured`` layer still validates the answer.
``achat_completion`` is the OpenAI-shaped adapter.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable
from urllib.parse import quote

from mas.library.standard.plugins.llm.http_protocol import (
    HttpLlmProtocol,
    cache_breakpoints,
    catalog_supports,
    normalize_usage,
    sampling_value,
)
from mas.library.standard.plugins.llm.thinking import (
    AnthropicThinking,
    anthropic_thinking,
    loop_replayable,
    thinking_off,
)
from mas.runtime.engine.llm_reasoning import ReasoningSettings, sanitize_assistant_message
from mas.runtime.engine.llm_request import apply_sampling_payload, merge_extra_body, sampling_settings_from_entry
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass
from mas.runtime.secrets.ref import SecretRef

logger = logging.getLogger(__name__)

_REASONING_KEY = "bedrock_reasoning_blocks"
_STOP = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "content_filtered": "content_filter",
    "guardrail_intervened": "content_filter",
}


class BedrockLLMProvider(HttpLlmProtocol):
    """Bedrock Converse layer. ``protocol: bedrock`` plus a url is ``[http, bedrock]``."""

    provider_id = "bedrock"
    kind = "bedrock"
    default_api_base = "https://bedrock-runtime.us-east-1.amazonaws.com"
    default_secret = SecretRef(name="bedrock")

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
        **_: Any,
    ) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        del temperature  # the call's default 0.7 is not a choice; only `sampling` is sent
        url = f"{self.api_base.rstrip('/')}/model/{quote(model, safe='')}/converse"
        cap = max_completion_tokens if max_completion_tokens is not None else max_tokens
        thinking = _thinking(model, reasoning, cap)
        if thinking.active and not loop_replayable(messages, _REASONING_KEY):
            thinking = AnthropicThinking(effort=thinking.effort, always_on=thinking.always_on)
        system, mapped = _messages(messages, replay=thinking.always_on or not thinking_off(reasoning))
        inference: dict[str, Any] = {}
        value = None if thinking.constrains else sampling_value(sampling, model, "temperature")
        if value is not None:
            inference["temperature"] = value
        if cap is not None:
            inference["maxTokens"] = int(cap)
        if sampling:
            sampled = apply_sampling_payload({}, sampling_settings_from_entry(sampling, model=model), model=model)
            if sampled.get("top_p") is not None and not thinking.constrains:
                inference["topP"] = sampled["top_p"]
            if sampled.get("stop"):
                inference["stopSequences"] = (
                    list(sampled["stop"]) if not isinstance(sampled["stop"], str) else [sampled["stop"]]
                )
        payload: dict[str, Any] = {"messages": mapped}
        if inference:
            payload["inferenceConfig"] = inference
        if system:
            payload["system"] = system
        # Converse needs toolConfig whenever the history holds tool blocks, even when no more calls are wanted.
        mapped_tools = _tools(tools) if tool_choice != "none" or _has_tool_blocks(mapped) else []
        if mapped_tools:
            config: dict[str, Any] = {"tools": mapped_tools}
            choice = _tool_choice(tool_choice) if tool_choice != "none" else None
            if choice and not (thinking.constrains and ("tool" in choice or "any" in choice)):
                config["toolChoice"] = choice
            payload["toolConfig"] = config
        extra = merge_extra_body(getattr(self, "extra_body", None), extra_body)
        if extra:
            payload.update(extra)
        payload.pop("response_format", None)
        fields = (
            dict(payload["additionalModelRequestFields"])
            if isinstance(payload.get("additionalModelRequestFields"), dict)
            else {}
        )
        if thinking.thinking:
            fields.setdefault("thinking", thinking.thinking)
        if thinking.effort:
            fields.setdefault("output_config", {"effort": thinking.effort})
        if fields:
            payload["additionalModelRequestFields"] = fields
        _add_cache_points(
            payload, cache_breakpoints(sampling, model, default_unknown=False), _cache_ttl(sampling, model)
        )
        headers = {
            **self._key_headers(),
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        query = {str(k): v for k, v in extra_query.items()} if extra_query else None
        return url, payload, headers, query

    def _message_from_completion(self, data: dict[str, Any], reasoning: ReasoningSettings, **_: Any) -> dict[str, Any]:
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        message_raw = output.get("message") if isinstance(output.get("message"), dict) else {}
        texts: list[str] = []
        thoughts: list[str] = []
        signed: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for block in message_raw.get("content") or []:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("reasoningContent"), dict):
                signed.append({"reasoningContent": dict(block["reasoningContent"])})
                text = (block["reasoningContent"].get("reasoningText") or {}).get("text")
                if text:
                    thoughts.append(str(text))
                continue
            if block.get("text"):
                texts.append(str(block["text"]))
            mapped = _tool_call(block.get("toolUse"))
            if mapped:
                tool_calls.append(mapped)
        message: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
        if thoughts:
            message["reasoning"] = "\n".join(thoughts)
        if tool_calls:
            message["tool_calls"] = tool_calls
            if signed:
                message["provider_state"] = {_REASONING_KEY: signed}
        usage = _usage(data.get("usage"))
        if usage:
            message["usage"] = usage
        stop = data.get("stopReason")
        if stop:
            message["finish_reason"] = _STOP.get(str(stop), str(stop))
        if str(stop or "") == "max_tokens" and tool_calls:
            raise ClassifiedFailure(
                "The response hit maxTokens while writing a tool call; raise max_tokens or split the task.",
                failure_class=FailureClass.APPLICATION,
                code="TRUNCATED_TOOL_CALL",
            )
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
        del extra
        from mas.library.standard.plugins.llm.http import arequest_once

        resp = await arequest_once(
            self._get_async_client(),
            "POST",
            url,
            json=payload,
            headers=headers,
            params=params,
            get_client=self._get_async_client,
            on_failure=self._adiscard_client_after_transport_failure,
        )
        resp.raise_for_status()
        message = self._message_from_completion(resp.json(), reasoning)
        text = str(message.get("content") or "")
        if text and callable(on_stream_chunk):
            try:
                on_stream_chunk(text)
            except Exception:
                logger.exception("on_stream_chunk callback failed")
        return message


def _thinking(model: str, reasoning: ReasoningSettings, cap: int | None) -> AnthropicThinking:
    """Anthropic-style thinking request for a model the catalog lists with a thinking mode; none otherwise."""
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    info = default_model_catalog().get(model)
    if info is None or not info.thinking_mode:
        return AnthropicThinking()
    return anthropic_thinking(model, reasoning, max_tokens=int(cap) if cap is not None else 8192)


def _has_tool_blocks(messages: list[dict[str, Any]]) -> bool:
    return any("toolUse" in b or "toolResult" in b for m in messages for b in m.get("content") or [])


def _cache_ttl(sampling: dict[str, Any] | None, model: str) -> str | None:
    """Converse ``ttl`` for ``prompt_cache_retention``: ``5m`` or ``1h`` (``24h`` is the maximum, 1h); none if unset."""
    retention = (sampling or {}).get("prompt_cache_retention")
    if retention is None or not catalog_supports(model, "prompt_cache_retention"):
        return None
    return "1h" if retention in {"1h", "24h"} else "5m"


def _add_cache_points(payload: dict[str, Any], places: tuple[bool, bool, int], ttl: str | None) -> None:
    """``cachePoint`` blocks after the system content, the last tool spec and the last user turns."""
    on_system, on_tools, on_messages = places
    point: dict[str, Any] = {"type": "default"}
    if ttl:
        point["ttl"] = ttl
    if on_system and payload.get("system"):
        payload["system"].append({"cachePoint": dict(point)})
    tools = (payload.get("toolConfig") or {}).get("tools")
    if on_tools and tools:
        tools.append({"cachePoint": dict(point)})
    marked = 0
    for message in reversed(payload.get("messages") or []):
        if marked >= on_messages:
            break
        if message.get("role") == "user" and message.get("content"):
            message["content"].append({"cachePoint": dict(point)})
            marked += 1


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "".join(parts)
    return "" if content is None else str(content)


def _messages(
    messages: list[dict[str, Any]], *, replay: bool = True
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    system: list[dict[str, str]] = []
    out: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        """Converse requires alternating roles and non-blank text: merge neighbours, drop empties."""
        blocks = [b for b in blocks if "text" not in b or str(b["text"]).strip()]
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
            return
        out.append({"role": role, "content": blocks})

    def flush_tools() -> None:
        if pending:
            add("user", list(pending))
            pending.clear()

    for raw in messages:
        role = str(raw.get("role") or "")
        if role == "system":
            text = _text_of(raw.get("content"))
            if text:
                system.append({"text": text})
            continue
        if role == "tool":
            result: dict[str, Any] = {
                "toolUseId": str(raw.get("tool_call_id") or raw.get("id") or ""),
                "content": [{"text": _text_of(raw.get("content")).strip() or "(no output)"}],
            }
            if raw.get("is_error"):
                result["status"] = "error"
            pending.append({"toolResult": result})
            continue
        flush_tools()
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            state = raw.get("provider_state")
            if replay and isinstance(state, dict) and isinstance(state.get(_REASONING_KEY), list):
                blocks.extend(dict(b) for b in state[_REASONING_KEY] if isinstance(b, dict))
            text = _text_of(raw.get("content"))
            if text:
                blocks.append({"text": text})
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
                        "toolUse": {
                            "toolUseId": str(call.get("id") or ""),
                            "name": str(fn.get("name") or ""),
                            "input": args,
                        }
                    }
                )
            add("assistant", blocks)
            continue
        add("user", [{"text": _text_of(raw.get("content"))}])
    flush_tools()
    return system, out


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
            {
                "toolSpec": {
                    "name": name,
                    "description": str(fn.get("description") or ""),
                    "inputSchema": {"json": schema},
                }
            }
        )
    return mapped


def _tool_choice(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice in (None, "auto"):
        return None
    if tool_choice == "none":
        return None
    if tool_choice == "required":
        return {"any": {}}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else tool_choice
        name = str(fn.get("name") or "").strip()
        if name:
            return {"tool": {"name": name}}
    return None


def _tool_call(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    args = raw.get("input")
    if not isinstance(args, dict):
        args = {}
    return {
        "id": str(raw.get("toolUseId") or ""),
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, separators=(",", ":"))},
    }


def _usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    prompt = raw.get("inputTokens")
    completion = raw.get("outputTokens")
    out = normalize_usage(raw)
    if prompt is not None:
        cached = int(out.get("cached_tokens") or 0) + int(out.get("cache_write_tokens") or 0)
        out["prompt_tokens"] = int(prompt) + cached
    if completion is not None:
        out["completion_tokens"] = int(completion)
    if prompt is not None or completion is not None:
        out["total_tokens"] = int(out.get("prompt_tokens") or 0) + int(completion or 0)
    return out
