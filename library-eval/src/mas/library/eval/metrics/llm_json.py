#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""JSON-mode calls through the shared OpenAI client, with parse retries."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mas.library.eval.errors import JudgeCallFailed, judge_error_text

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from *text*, tolerating fenced markdown."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT.search(stripped)
    if not match:
        raise ValueError("response is not a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("JSON value is not an object")
    return parsed


def complete_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    retries: int = 2,
) -> dict[str, Any]:
    """Complete a chat on the shared judge client and parse a JSON object.

    Uses :func:`mas.library.eval.mce.runner.judge_client` (configured by
    ``install_openai_llm_service``): the client of the current judge run, or one
    opened for this call. Retries on malformed JSON by appending a "JSON only" turn.
    """
    from mas.library.eval.mce.runner import (
        get_effective_judge_model,
        judge_client,
    )

    model = get_effective_judge_model()
    with judge_client() as client:
        return _complete_json_on(client, model, messages, temperature, max_tokens, retries)


def _complete_json_on(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    retries: int,
) -> dict[str, Any]:
    from mas.library.eval.mce.runner import paced_live_call

    conversation: list[dict[str, str]] = list(messages)
    last_error: Exception | None = None
    last_text = ""
    for _attempt in range(retries + 1):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        def _create(call_kwargs: dict[str, Any] = kwargs) -> Any:
            try:
                return client.chat.completions.create(**call_kwargs, response_format={"type": "json_object"})
            except Exception:
                logger.debug("JSON response_format unsupported; retrying without it")
                return client.chat.completions.create(**call_kwargs)

        failure: JudgeCallFailed | None = None
        try:
            response = paced_live_call(_create, model=model)
        except Exception as exc:
            failure = JudgeCallFailed(f"judge call failed (model={model!r}): {judge_error_text(exc)}")
        if failure is not None:
            raise failure
        last_text = ""
        try:
            last_text = response.choices[0].message.content or ""
        except Exception as exc:
            last_error = exc
            continue
        try:
            return extract_json_object(last_text)
        except Exception as exc:
            last_error = exc
            conversation = conversation + [
                {"role": "assistant", "content": last_text},
                {
                    "role": "user",
                    "content": "Retry. Return a single JSON object only, no markdown.",
                },
            ]
    raise JudgeCallFailed(
        f"LLM did not return valid JSON after {retries + 1} attempt(s): {last_error}; last_text={last_text[:200]!r}"
    )
