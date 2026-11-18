#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Google Gemini generateContent as a protocol layer over an http transport.

Speaks ``POST {api_base}/models/{model}:generateContent``. Posts through
``inner.aclient``. ``achat_completion`` is the OpenAI-shaped adapter.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from mas.library.standard.plugins.llm.http_protocol import (
    HttpLlmProtocol,
    araise_for_status,
    normalize_usage,
    sampling_value,
)
from mas.library.standard.plugins.llm.structured import schema_from_format
from mas.library.standard.plugins.llm.thinking import gemini_thinking_config
from mas.runtime.engine.llm_reasoning import ReasoningSettings, sanitize_assistant_message
from mas.runtime.engine.llm_request import apply_sampling_payload, merge_extra_body, sampling_settings_from_entry
from mas.runtime.reliability.classes import ClassifiedFailure, FailureClass

logger = logging.getLogger(__name__)

_CACHED_CONTENT_PREFIXES = ("cachedContents/", "projects/")
_SKIP_SIGNATURE = "skip_thought_signature_validator"
_SIGNATURES_KEY = "gemini_thought_signatures"
_STOP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "BLOCKLIST": "content_filter",
    "SPII": "content_filter",
    "OTHER": "stop",
}


class GeminiLLMProvider(HttpLlmProtocol):
    """Gemini generateContent layer. ``protocol: gemini`` plus a url is ``[http, gemini]``."""

    provider_id = "gemini"
    kind = "gemini"
    default_api_base = "https://generativelanguage.googleapis.com/v1beta"
    default_api_key_env = "GEMINI_API_KEY"

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
        stream: bool = False,
        response_format: Any = None,
        **_: Any,
    ) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        del temperature  # the call's default 0.7 is not a choice; only `sampling` is sent
        url = self._request_url(model, stream=stream)
        system, contents = _contents(messages)
        cap = max_completion_tokens if max_completion_tokens is not None else max_tokens
        generation: dict[str, Any] = {}
        value = sampling_value(sampling, model, "temperature")
        if value is not None:
            generation["temperature"] = value
        if cap is not None:
            generation["maxOutputTokens"] = int(cap)
        fmt = response_format
        if sampling:
            sampled = apply_sampling_payload({}, sampling_settings_from_entry(sampling, model=model), model=model)
            if sampled.get("top_p") is not None:
                generation["topP"] = sampled["top_p"]
            if sampled.get("top_k") is not None:
                generation["topK"] = sampled["top_k"]
            if sampled.get("stop"):
                generation["stopSequences"] = (
                    list(sampled["stop"]) if not isinstance(sampled["stop"], str) else [sampled["stop"]]
                )
            fmt = sampled.get("response_format", fmt)
        schema = schema_from_format(fmt)
        thinking = gemini_thinking_config(model, reasoning)
        if thinking:
            generation["thinkingConfig"] = thinking
        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        cached = (sampling or {}).get("prompt_cache_key")
        use_cache = isinstance(cached, str) and cached.startswith(_CACHED_CONTENT_PREFIXES)
        if use_cache:
            # The cached content already holds the system instruction and the tools; the API rejects them here.
            payload["cachedContent"] = cached
        else:
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            decls = _tools(tools)
            if decls:
                payload["tools"] = [{"functionDeclarations": decls}]
            config = _tool_config(tool_choice)
            if config:
                payload["toolConfig"] = config
        extra = merge_extra_body(getattr(self, "extra_body", None), extra_body)
        if extra:
            payload.update(extra)
        payload.pop("response_format", None)
        if schema:
            gen = payload.get("generationConfig")
            if not isinstance(gen, dict):
                gen = {}
                payload["generationConfig"] = gen
            gen["responseMimeType"] = "application/json"
            gen["responseJsonSchema"] = schema
        headers = self._auth_headers(api_key)
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        query: dict[str, Any] = dict(extra_query) if extra_query else {}
        if stream:
            query["alt"] = "sse"
        return url, payload, headers, query or None

    def _request_url(self, model: str, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{self.api_base.rstrip('/')}/models/{_model_id(model)}:{action}"

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def _message_from_completion(self, data: dict[str, Any], reasoning: ReasoningSettings, **_: Any) -> dict[str, Any]:
        _raise_embedded_error(data)
        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        acc = _Parts()
        for part in parts:
            acc.add(part, None)
        message: dict[str, Any] = acc.message_fields()
        usage = _usage(data.get("usageMetadata"))
        if usage:
            message["usage"] = usage
        finish = ""
        if candidates:
            finish = _STOP.get(
                str(candidates[0].get("finishReason") or ""), str(candidates[0].get("finishReason") or "")
            )
        elif (data.get("promptFeedback") or {}).get("blockReason"):
            finish = "content_filter"
        if candidates:
            _check_finish(candidates[0].get("finishReason"))
        if finish:
            message["finish_reason"] = finish
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
        acc = _StreamAcc()
        try:
            async with self._get_async_client().stream(
                "POST", url, json=payload, headers=headers, params=params
            ) as resp:
                await araise_for_status(resp)
                async for raw_line in resp.aiter_lines():
                    acc.feed(raw_line, on_stream_chunk)
        except ClassifiedFailure:
            raise
        except Exception as exc:
            await self._areraise_stream_failure(exc, emitted_any=acc.emitted_any)
        return acc.message(reasoning)


def _raise_embedded_error(data: dict[str, Any]) -> None:
    """Google can answer HTTP 200 with an ``error`` body; that is a failed call, not an empty completion."""
    err = data.get("error")
    if not isinstance(err, dict):
        return
    code = int(err.get("code") or 0)
    transient = code in {429, 500, 502, 503, 504}
    raise ClassifiedFailure(
        f"Gemini error {code or ''} {err.get('status') or ''}: {err.get('message') or 'no detail'}".replace("  ", " "),
        failure_class=FailureClass.TRANSIENT if transient else FailureClass.APPLICATION,
        code=str(err.get("status") or code or "error"),
    )


class _Parts:
    """Collects the parts of one answer, whole or streamed: text, thoughts, tool calls, thought signatures."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.thoughts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.signatures: list[str] = []
        self.emitted_any = False

    def add(self, part: Any, on_stream_chunk: Callable[[str], None] | None) -> None:
        if not isinstance(part, dict):
            return
        text = part.get("text")
        if text and part.get("thought"):
            self.thoughts.append(str(text))
        elif text:
            self.texts.append(str(text))
            self.emitted_any = True
            if callable(on_stream_chunk):
                try:
                    on_stream_chunk(str(text))
                except Exception:
                    logger.exception("on_stream_chunk callback failed")
        mapped = _tool_call(part.get("functionCall"), len(self.tool_calls))
        if mapped:
            self.emitted_any = True
            self.tool_calls.append(mapped)
            self.signatures.append(str(part.get("thoughtSignature") or ""))

    def message_fields(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.texts) or None}
        if self.thoughts:
            message["reasoning"] = "\n".join(self.thoughts)
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
            if any(self.signatures):
                message["provider_state"] = {_SIGNATURES_KEY: list(self.signatures)}
        return message


_BAD_FINISH = frozenset({"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"})


def _check_finish(finish: Any) -> None:
    """A function call the model could not form is a failed call, not an empty turn."""
    if str(finish or "") in _BAD_FINISH:
        raise ClassifiedFailure(
            f"Gemini could not form the tool call ({finish}); retry or simplify the tool schema.",
            failure_class=FailureClass.APPLICATION,
            code=str(finish),
        )


def _model_id(model: str) -> str:
    name = str(model or "").strip()
    return name.split("models/", 1)[-1] if name.startswith("models/") else name


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


def _contents(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []

    def append(role: str, parts: list[dict[str, Any]]) -> None:
        if not parts:
            return
        if out and out[-1]["role"] == role:
            out[-1]["parts"].extend(parts)
            return
        out.append({"role": role, "parts": parts})

    for raw in messages:
        role = str(raw.get("role") or "")
        if role == "system":
            text = _text_of(raw.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "assistant":
            parts: list[dict[str, Any]] = []
            text = _text_of(raw.get("content"))
            if text:
                parts.append({"text": text})
            state = raw.get("provider_state")
            signed = state.get(_SIGNATURES_KEY) if isinstance(state, dict) else None
            for index, call in enumerate(raw.get("tool_calls") or []):
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                cid = str(call.get("id") or "")
                name = str(fn.get("name") or "")
                if cid:
                    names[cid] = name
                signature = signed[index] if isinstance(signed, list) and index < len(signed) else ""
                parts.append(
                    {"functionCall": {"name": name, "args": args}, "thoughtSignature": signature or _SKIP_SIGNATURE}
                )
            append("model", parts)
            continue
        if role == "tool":
            cid = str(raw.get("tool_call_id") or "")
            name = names.get(cid) or str(raw.get("name") or cid)
            try:
                response = json.loads(_text_of(raw.get("content")) or "{}")
            except (TypeError, ValueError):
                response = {"result": _text_of(raw.get("content"))}
            if not isinstance(response, dict):
                response = {"result": response}
            if raw.get("is_error"):
                response = {"error": response.get("result", response)}
            append("user", [{"functionResponse": {"name": name, "response": response}}])
            continue
        user_text = _text_of(raw.get("content"))
        if user_text.strip():
            append("user", [{"text": user_text}])
    return "\n".join(system_parts), out


def _tool_call(call: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    return {
        "id": str(call.get("id") or f"gemini_call_{index}"),
        "type": "function",
        "function": {"name": str(call.get("name") or ""), "arguments": json.dumps(args, default=str)},
    }


def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    decls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        decl: dict[str, Any] = {"name": name, "description": str(fn.get("description") or "")}
        if isinstance(fn.get("parameters"), dict):
            decl["parameters"] = fn["parameters"]
        decls.append(decl)
    return decls


def _tool_config(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice in (None, "auto"):
        return None
    if tool_choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if tool_choice == "required":
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else tool_choice
        name = str(fn.get("name") or "").strip()
        if name:
            return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    return None


def _usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    prompt = raw.get("promptTokenCount")
    completion = raw.get("candidatesTokenCount")
    total = raw.get("totalTokenCount")
    out = normalize_usage(raw)
    if prompt is not None:
        out["prompt_tokens"] = int(prompt)
    if completion is not None:
        out["completion_tokens"] = int(completion)
    if total is not None:
        out["total_tokens"] = int(total)
    elif prompt is not None or completion is not None:
        out["total_tokens"] = int(prompt or 0) + int(completion or 0)
    return out


class _StreamAcc:
    def __init__(self) -> None:
        self.parts = _Parts()
        self.usage: dict[str, Any] = {}
        self.finish_reason = ""
        self.raw_finish = ""

    @property
    def emitted_any(self) -> bool:
        return self.parts.emitted_any

    def feed(self, raw_line: Any, on_stream_chunk: Callable[[str], None] | None) -> None:
        chunk = _sse_json(raw_line)
        if not chunk:
            return
        _raise_embedded_error(chunk)
        usage = _usage(chunk.get("usageMetadata"))
        if usage:
            self.usage.update(usage)
        candidates = chunk.get("candidates") or []
        if not candidates:
            if (chunk.get("promptFeedback") or {}).get("blockReason"):
                self.finish_reason = "content_filter"
            return
        finish = candidates[0].get("finishReason")
        if finish:
            self.raw_finish = str(finish)
            self.finish_reason = _STOP.get(str(finish), str(finish))
        for part in (candidates[0].get("content") or {}).get("parts") or []:
            self.parts.add(part, on_stream_chunk)

    def message(self, reasoning: ReasoningSettings) -> dict[str, Any]:
        message = self.parts.message_fields()
        if self.usage:
            message["usage"] = self.usage
        if self.finish_reason:
            message["finish_reason"] = self.finish_reason
        _check_finish(self.raw_finish)
        return sanitize_assistant_message(message, exclude=reasoning.exclude)


def _sse_json(raw_line: Any) -> dict[str, Any] | None:
    line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
    if line.startswith("data:"):
        line = line[len("data:") :].strip()
    else:
        line = line.strip()
    if not line or line == "[DONE]":
        return None
    try:
        chunk = json.loads(line)
    except (TypeError, ValueError):
        return None
    return chunk if isinstance(chunk, dict) else None
