#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible chat-completions LLM provider.

Speaks ``POST {api_base}/chat/completions`` (OpenAI, Ollama, LiteLLM, …).
Later vendors (Bedrock, …) register their own ``llm_provider`` plugin.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

import httpx
from mas.library.standard.plugins.llm.http import (
    arequest_with_retries,
    resolve_ssl_verify,
)
from mas.runtime.boundary.context.assemble import llm_tool_choice
from mas.runtime.engine.llm_reasoning import (
    ReasoningSettings,
    ThinkTagStreamFilter,
    apply_reasoning_payload,
    coerce_reasoning_settings,
    sanitize_assistant_message,
)
from mas.runtime.engine.llm_request import (
    apply_output_token_limit,
    apply_sampling_payload,
    merge_extra_body,
    sampling_settings_from_entry,
)
from mas.runtime.reliability.classify import (
    classify_llm_failure,
    classify_llm_http_error,
)
from mas.runtime.reliability.policy import llm_retry_policy

logger = logging.getLogger(__name__)

#: HTTP status codes worth retrying (rate limit + transient server failures).
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_REQUEST_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """True for dropped connections, timeouts, and transient HTTP failures."""
    if isinstance(exc, httpx.TransportError):
        # Covers ConnectError, ReadError, WriteError, RemoteProtocolError
        # (mid-response connection drops), PoolTimeout, TimeoutException, ...
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return False


class OpenAILLMProvider:
    """OpenAI-compatible HTTP protocol plugin."""

    provider_id = "openai"
    kind = "openai"

    def __init__(
        self,
        *,
        api_base: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        llm_proxy: dict[str, Any] | None = None,
        stream: bool = False,
        reasoning_effort: str | None = None,
        reasoning: ReasoningSettings | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        http_timeout: float | None = None,
        **_: Any,
    ) -> None:
        self.api_base = api_base or "https://api.openai.com/v1"
        self.api_key_env = api_key_env or "OPENAI_API_KEY"
        self.llm_proxy = llm_proxy
        self.stream = stream
        self.reasoning = coerce_reasoning_settings(reasoning, effort=reasoning_effort)
        self.reasoning_effort = self.reasoning.effort
        self.extra_body = dict(extra_body or {})
        self.http_timeout = http_timeout
        self._client: httpx.Client | None = None
        self._aclient: httpx.AsyncClient | None = None
        retry_raw = (llm_proxy or {}).get("retry") if isinstance(llm_proxy, dict) else None
        self._retry_policy = llm_retry_policy(**retry_raw) if isinstance(retry_raw, dict) else llm_retry_policy()
        raw_breaker = (llm_proxy or {}).get("circuit_breaker") if isinstance(llm_proxy, dict) else None
        self._circuit_breaker = raw_breaker if hasattr(raw_breaker, "before_call") else None

    def _timeout(self) -> float:
        proxy = self.llm_proxy if isinstance(self.llm_proxy, dict) else {}
        if proxy.get("timeout") is not None:
            return float(proxy["timeout"])
        if self.http_timeout is not None:
            return float(self.http_timeout)
        return 120.0

    def _get_client(self) -> httpx.Client:
        """Reused keep-alive client — one connection pool per provider instance."""
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout(), verify=resolve_ssl_verify(self.llm_proxy))
        return self._client

    def _get_async_client(self) -> httpx.AsyncClient:
        """Reused async keep-alive client — one connection pool per provider instance."""
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(timeout=self._timeout(), verify=resolve_ssl_verify(self.llm_proxy))
        return self._aclient

    def _send_with_retry(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Retry a single request on dropped connections, timeouts, 429/5xx.

        Non-retryable failures (4xx auth/validation errors, SSL errors that
        aren't transport drops, etc.) raise on the first attempt.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = send()
                resp.raise_for_status()
                return resp
            except Exception as exc:
                if attempt >= _MAX_REQUEST_ATTEMPTS or not _is_retryable_llm_error(exc):
                    raise
                delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "LLM request failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    _MAX_REQUEST_ATTEMPTS,
                    delay,
                    exc,
                )
                time.sleep(delay)

    @classmethod
    def from_provider_spec(cls, spec: dict[str, Any]) -> "OpenAILLMProvider":
        """Bind from ``spec.models[]`` (plus infra endpoint defaults), like tools."""
        return cls.from_infra_spec(spec)

    @classmethod
    def from_infra_spec(cls, spec: dict[str, Any]) -> "OpenAILLMProvider":
        """Bind from a resolved ``llm_proxy`` dict or infra ``spec``."""
        proxy = spec.get("proxy") if isinstance(spec.get("proxy"), dict) else spec
        llm_proxy = spec.get("llm_proxy") if isinstance(spec.get("llm_proxy"), dict) else spec
        timeout = None
        for src in (proxy, spec, llm_proxy):
            if isinstance(src, dict) and src.get("timeout") is not None:
                timeout = float(src["timeout"])
                break
        return cls(
            api_base=str(proxy.get("api_base") or spec.get("api_base") or "https://api.openai.com/v1"),
            api_key_env=str(proxy.get("api_key_env") or spec.get("api_key_env") or "OPENAI_API_KEY"),
            llm_proxy=llm_proxy if isinstance(llm_proxy, dict) else spec,
            stream=bool(spec.get("stream", False)),
            reasoning=spec.get("reasoning"),
            reasoning_effort=str(spec["reasoning_effort"]) if spec.get("reasoning_effort") else None,
            extra_body=spec.get("extra") if isinstance(spec.get("extra"), dict) else None,
            http_timeout=timeout,
        )

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        stream: bool | None = None,
        on_stream_chunk: Callable[[str], None] | None = None,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        reasoning: ReasoningSettings | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
        extra_query: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        tool_choice: Any = None,
        stream_options: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        key = api_key if api_key is not None else os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"Missing API key env {self.api_key_env} for live LLM.")
        use_stream = self.stream if stream is None else stream
        settings = coerce_reasoning_settings(
            reasoning if reasoning is not None else self.reasoning, effort=reasoning_effort
        )
        try:
            return self._post_chat(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                max_completion_tokens=max_completion_tokens,
                api_key=key,
                stream=use_stream,
                on_stream_chunk=on_stream_chunk,
                reasoning=settings,
                extra_body=extra_body,
                extra_headers=extra_headers,
                extra_query=extra_query,
                sampling=sampling,
                tool_choice=tool_choice,
                stream_options=stream_options,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            logger.debug("OpenAI-compatible LLM call failed", exc_info=True)
            raise RuntimeError(classify_llm_http_error(exc)) from exc

    async def achat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        stream: bool | None = None,
        on_stream_chunk: Callable[[str], None] | None = None,
        api_key: str | None = None,
        reasoning_effort: str | None = None,
        reasoning: ReasoningSettings | dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
        extra_query: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        tool_choice: Any = None,
        stream_options: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        key = api_key if api_key is not None else os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"Missing API key env {self.api_key_env} for live LLM.")
        use_stream = self.stream if stream is None else stream
        settings = coerce_reasoning_settings(
            reasoning if reasoning is not None else self.reasoning, effort=reasoning_effort
        )
        try:
            return await self._apost_chat(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                max_completion_tokens=max_completion_tokens,
                api_key=key,
                stream=use_stream,
                on_stream_chunk=on_stream_chunk,
                reasoning=settings,
                extra_body=extra_body,
                extra_headers=extra_headers,
                extra_query=extra_query,
                sampling=sampling,
                tool_choice=tool_choice,
                stream_options=stream_options,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            logger.debug("OpenAI-compatible LLM call failed", exc_info=True)
            raise RuntimeError(classify_llm_http_error(exc)) from exc

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
    ) -> tuple[str, dict[str, Any], dict[str, str], dict[str, Any] | None]:
        url = self.api_base.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = apply_reasoning_payload(
            {"model": model, "messages": messages},
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
    def _message_from_completion(data: dict[str, Any], reasoning: ReasoningSettings) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            return {}
        message = dict(choices[0].get("message") or {})
        usage = data.get("usage") or {}
        if usage:
            message["usage"] = usage
        finish_reason = choices[0].get("finish_reason")
        if finish_reason:
            message["finish_reason"] = finish_reason
        return sanitize_assistant_message(message, exclude=reasoning.exclude)

    def _post_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        max_completion_tokens: int | None,
        api_key: str,
        stream: bool,
        on_stream_chunk: Callable[[str], None] | None,
        reasoning: ReasoningSettings,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
        extra_query: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        tool_choice: Any = None,
        stream_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url, payload, headers, query = self._build_chat_request(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            api_key=api_key,
            reasoning=reasoning,
            extra_body=extra_body,
            extra_headers=extra_headers,
            extra_query=extra_query,
            sampling=sampling,
            tool_choice=tool_choice,
            stream_options=stream_options,
        )
        if stream:
            return self._chat_completion_streamed(
                url,
                payload,
                headers,
                on_stream_chunk=on_stream_chunk,
                reasoning=reasoning,
                params=query,
            )
        resp = self._send_with_retry(
            lambda: self._get_client().post(url, json=payload, headers=headers, params=query)
        )
        return self._message_from_completion(resp.json(), reasoning)

    async def _apost_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int | None,
        max_completion_tokens: int | None,
        api_key: str,
        stream: bool,
        on_stream_chunk: Callable[[str], None] | None,
        reasoning: ReasoningSettings,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, Any] | None = None,
        extra_query: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        tool_choice: Any = None,
        stream_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url, payload, headers, query = self._build_chat_request(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            api_key=api_key,
            reasoning=reasoning,
            extra_body=extra_body,
            extra_headers=extra_headers,
            extra_query=extra_query,
            sampling=sampling,
            tool_choice=tool_choice,
            stream_options=stream_options,
        )
        if stream:
            from mas.runtime.reliability.retry import acall_with_retry

            return await acall_with_retry(
                lambda: self._achat_completion_streamed(
                    url,
                    payload,
                    headers,
                    on_stream_chunk=on_stream_chunk,
                    reasoning=reasoning,
                    params=query,
                ),
                policy=self._retry_policy,
                classify=classify_llm_failure,
                breaker=self._circuit_breaker,
                target=f"llm:{self.api_base}",
                idempotent=True,
            )
        resp = await arequest_with_retries(
            self._get_async_client(),
            "POST",
            url,
            json=payload,
            headers=headers,
            params=query,
            retry_policy=self._retry_policy,
            circuit_breaker=self._circuit_breaker,
            retry_target=f"llm:{self.api_base}",
        )
        resp.raise_for_status()
        return self._message_from_completion(resp.json(), reasoning)

    def _chat_completion_streamed(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        on_stream_chunk: Callable[[str], None] | None,
        reasoning: ReasoningSettings,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """SSE-streamed variant — same OpenAI-compatible message shape.

        A dropped connection / timeout is only retried while nothing has yet
        been surfaced to ``on_stream_chunk`` — once the caller has seen
        partial output, re-sending the request would duplicate/corrupt it.
        """
        stream_payload = dict(payload)
        stream_payload["stream"] = True

        attempt = 0
        while True:
            attempt += 1
            content_parts: list[str] = []
            tool_call_accum: dict[int, dict[str, Any]] = {}
            finish_reason = ""
            usage: dict[str, Any] = {}
            think_filter = ThinkTagStreamFilter() if reasoning.exclude else None
            emitted_any = False
            try:
                with self._get_client().stream(
                    "POST", url, json=stream_payload, headers=headers, params=params
                ) as resp:
                    resp.raise_for_status()
                    for raw_line in resp.iter_lines():
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
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if text:
                            if think_filter is not None:
                                text = think_filter.feed(text)
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
                if emitted_any or attempt >= _MAX_REQUEST_ATTEMPTS or not _is_retryable_llm_error(exc):
                    raise
                delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Streamed LLM request failed before any output (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    _MAX_REQUEST_ATTEMPTS,
                    delay,
                    exc,
                )
                time.sleep(delay)
                continue

            message: dict[str, Any] = {"content": "".join(content_parts) or None}
            if tool_call_accum:
                message["tool_calls"] = [tool_call_accum[i] for i in sorted(tool_call_accum)]
            if usage:
                message["usage"] = usage
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
    ) -> dict[str, Any]:
        """Async SSE-streamed variant — same OpenAI-compatible message shape."""
        stream_payload = dict(payload)
        stream_payload["stream"] = True

        content_parts: list[str] = []
        tool_call_accum: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        usage: dict[str, Any] = {}
        think_filter = ThinkTagStreamFilter() if reasoning.exclude else None

        async with self._get_async_client().stream(
            "POST", url, json=stream_payload, headers=headers, params=params
        ) as resp:
            resp.raise_for_status()
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
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                fr = choice.get("finish_reason")
                if fr:
                    finish_reason = fr
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    if think_filter is not None:
                        text = think_filter.feed(text)
                    if text:
                        content_parts.append(text)
                        if callable(on_stream_chunk):
                            try:
                                on_stream_chunk(text)
                            except Exception:
                                logger.exception("on_stream_chunk callback failed")
                for tc in delta.get("tool_calls") or []:
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

        message: dict[str, Any] = {"content": "".join(content_parts) or None}
        if tool_call_accum:
            message["tool_calls"] = [tool_call_accum[i] for i in sorted(tool_call_accum)]
        if usage:
            message["usage"] = usage
        if finish_reason:
            message["finish_reason"] = finish_reason
        return sanitize_assistant_message(message, exclude=reasoning.exclude)
||||||| parent of d34b455d (fix(llm): retry openai provider requests on dropped connections)
        with self._get_client().stream("POST", url, json=stream_payload, headers=headers, params=params) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
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
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                fr = choice.get("finish_reason")
                if fr:
                    finish_reason = fr
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    if think_filter is not None:
                        text = think_filter.feed(text)
                    if text:
                        content_parts.append(text)
                        if callable(on_stream_chunk):
                            try:
                                on_stream_chunk(text)
                            except Exception:
                                logger.exception("on_stream_chunk callback failed")
                for tc in delta.get("tool_calls") or []:
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

        message: dict[str, Any] = {"content": "".join(content_parts) or None}
        if tool_call_accum:
            message["tool_calls"] = [tool_call_accum[i] for i in sorted(tool_call_accum)]
        if usage:
            message["usage"] = usage
        if finish_reason:
            message["finish_reason"] = finish_reason
        return sanitize_assistant_message(message, exclude=reasoning.exclude)
