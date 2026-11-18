#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Map portable ``reasoning`` settings onto a vendor's thinking request.

The catalog entry for a model says how it thinks (``thinking_mode``):

* ``adaptive``: the model decides how long to think; ``reasoning.effort`` bounds it.
* ``always_on``: thinking cannot be turned off; ``reasoning.effort`` bounds it.
* ``budget``: thinking is on with a token budget (``reasoning.budget_tokens``).
* ``level``: a discrete level (``low`` / ``high``).
* ``none``: the model does not think.

A model the catalog does not list is treated as ``adaptive``, or ``budget`` when
the spec sets ``reasoning.budget_tokens``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mas.runtime.engine.llm_reasoning import ReasoningSettings

_DISABLED = frozenset({"none", "disable"})
_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
_BUDGET_BY_EFFORT = {"low": 4000, "medium": 10000, "high": 16000, "xhigh": 32000, "max": 32000}
MIN_BUDGET = 1024
MIN_ANSWER_TOKENS = 1024


def thinking_mode(model: str, reasoning: ReasoningSettings) -> str:
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    info = default_model_catalog().get(model)
    if info is not None and info.thinking_mode:
        return info.thinking_mode
    return "budget" if reasoning.budget_tokens else "adaptive"


def wants_thinking(reasoning: ReasoningSettings) -> bool:
    """True when the spec asks for thinking: an effort, ``think``, or a token budget, and not turned off."""
    if reasoning.think is False or str(reasoning.effort or "").lower() in _DISABLED:
        return False
    return reasoning.thinking_enabled() or bool(reasoning.budget_tokens)


def effort_name(reasoning: ReasoningSettings) -> str | None:
    """``low`` .. ``max`` for a requested effort, ``None`` when none was set or thinking is off."""
    if not wants_thinking(reasoning):
        return None
    return _EFFORT.get(str(reasoning.effort or "").lower())


@dataclass(frozen=True)
class AnthropicThinking:
    """The ``thinking`` object and ``output_config.effort`` to send, either of which may be absent."""

    thinking: dict[str, Any] | None = None
    effort: str | None = None
    always_on: bool = False

    @property
    def active(self) -> bool:
        """True when the request carries a thinking config."""
        return self.thinking is not None

    @property
    def constrains(self) -> bool:
        """True when the model will think on this request: it then rejects sampling and forced tool choice."""
        return self.thinking is not None or self.always_on


def thinking_off(reasoning: ReasoningSettings) -> bool:
    """True when the spec asked for thinking to be off (effort ``none`` / ``disable``, or ``think: false``)."""
    return reasoning.think is False or str(reasoning.effort or "").lower() in _DISABLED


def anthropic_thinking(model: str, reasoning: ReasoningSettings, *, max_tokens: int) -> AnthropicThinking:
    """The thinking request for an Anthropic-shaped model; empty when thinking is off or unsupported.

    ``max_tokens`` is the output cap the request will carry. A budget must leave
    ``MIN_ANSWER_TOKENS`` of it for the answer, otherwise thinking is left out.
    """
    mode = thinking_mode(model, reasoning)
    if mode == "always_on":
        display = {} if reasoning.exclude else {"display": "summarized"}
        return AnthropicThinking(
            thinking={"type": "adaptive", **display} if display else None,
            effort=effort_name(reasoning),
            always_on=True,
        )
    if mode in {"none", "level"} or not wants_thinking(reasoning):
        return AnthropicThinking()
    display = {} if reasoning.exclude else {"display": "summarized"}
    effort = effort_name(reasoning)
    if mode == "adaptive":
        return AnthropicThinking(thinking={"type": "adaptive", **display}, effort=effort)
    budget = reasoning.budget_tokens or _BUDGET_BY_EFFORT.get(effort or "medium", _BUDGET_BY_EFFORT["medium"])
    budget = min(int(budget), max_tokens - MIN_ANSWER_TOKENS)
    if budget < MIN_BUDGET:
        return AnthropicThinking()
    return AnthropicThinking(thinking={"type": "enabled", "budget_tokens": budget, **display})


def gemini_thinking_config(model: str, reasoning: ReasoningSettings) -> dict[str, Any] | None:
    """``generationConfig.thinkingConfig`` for a Gemini-shaped model, or ``None`` to send nothing."""
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    mode = thinking_mode(model, reasoning)
    include = {} if reasoning.exclude else {"includeThoughts": True}
    info = default_model_catalog().get(model)
    if not wants_thinking(reasoning):
        if reasoning.effort in _DISABLED and mode == "budget":
            setting = info.setting("reasoning.budget_tokens") if info is not None else None
            if setting is not None and setting.minimum == 0:
                return {"thinkingBudget": 0}
        return None
    if mode == "level":
        effort = effort_name(reasoning)
        return {"thinkingLevel": "high" if effort in {"high", "xhigh", "max"} else "low", **include}
    if mode == "budget":
        budget = reasoning.budget_tokens
        effort = effort_name(reasoning)
        if budget is None and effort:
            budget = _BUDGET_BY_EFFORT[effort]
        if budget is None and info is not None:
            budget = info.defaults.get("reasoning.budget_tokens")
        if budget is None:
            budget = _BUDGET_BY_EFFORT["medium"]
        if info is not None:
            budget = info.clamp("reasoning.budget_tokens", budget)
        return {"thinkingBudget": int(budget), **include}
    return {**include} or None


def loop_replayable(messages: list[dict[str, Any]], key: str) -> bool:
    """False when a tool turn of the current loop lost the provider state stored under ``key``.

    A resumed or older session has assistant tool turns without it. Anthropic-shaped APIs reject such a
    turn while thinking is on, so the request then goes out with thinking left off.
    """
    for message in reversed(messages):
        role = message.get("role")
        if role == "user":
            return True
        if role == "assistant" and message.get("tool_calls"):
            state = message.get("provider_state")
            if not (isinstance(state, dict) and state.get(key)):
                return False
    return True
