#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Summarizer sub-plugins for ``SummarizingConversation``.

Registry type ``summarizer``. Bound by ``CMFactory`` onto a summarising
context manager — not a standalone spec slot.

- ``llm`` (default): this agent's engine (``summarize_messages``). Optional
  ``params.model`` / ``params.instructions`` override the summary LLM and
  system prompt. Trigger is the history token budget, not this prompt.
- ``drop``: discard older turns; keep ``keep_turns`` verbatim.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from mas.runtime.aio import settle
from mas.runtime.contracts.content_types import text_of

_log = logging.getLogger(__name__)


def _text_view(messages: list[dict[str, Any]]) -> str:
    """Text-only view of a conversation. A summary never carries media."""
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "?")
        body = text_of(msg.get("content"), media="placeholder")
        lines.append(f"{role}: {body}")
    return "\n".join(lines)


def _accepts_kwarg(fn: Any, name: str) -> bool:
    """Whether callable *fn* accepts keyword argument *name*."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return True
    return name in {p.name for p in params}


SUMMARIZE_INSTRUCTIONS = (
    "Summarize the following conversation turns concisely, preserving key "
    "facts, decisions, and any identifiers (names, IDs, numbers) a later "
    "turn might need to reference. Write plain prose, not a transcript."
)


class DropSummarizer:
    """Discard older turns. The context manager keeps ``keep_turns`` verbatim."""

    def summarize(self, messages: list[dict[str, Any]]) -> str | None:
        _ = messages
        return None


class LlmSummarizer:
    """Summarize older turns with an LLM.

    Default model is the **summarizer slot**, else **this agent's** live
    engine. Slot chain: Agent ``spec.models[id=summarizer]`` → MAS →
    ``experiment.models.summarizer`` → resolved turn model. ``params.model``
    overrides that default (a ``spec.models[].id`` or a LiteLLM string,
    resolved by ``CMFactory`` before ``bind_engine``). ``instructions``
    overrides the package system prompt. Without ``asummarize_messages`` this
    degrades to drop and logs a warning.
    """

    def __init__(self, model: str | None = None, instructions: str | None = None) -> None:
        self._engine: Any | None = None
        self._model_ref = str(model).strip() if model else None
        text = str(instructions).strip() if instructions else ""
        self._instructions = text or None
        self._resolved_model: str | None = None
        self._model_source: str = "agent"
        self._warned_no_engine = False

    @property
    def model(self) -> str | None:
        """Effective summary model (override or engine.model)."""
        if self._resolved_model:
            return self._resolved_model
        engine_model = getattr(self._engine, "model", None)
        return str(engine_model).strip() if engine_model else self._model_ref

    @property
    def model_source(self) -> str:
        return self._model_source

    @property
    def instructions(self) -> str:
        """System prompt for the summary call (override or package default)."""
        return self._instructions or SUMMARIZE_INSTRUCTIONS

    def bind_engine(
        self,
        engine: Any | None,
        *,
        model: str | None = None,
        model_source: str | None = None,
    ) -> None:
        self._engine = engine
        if model:
            self._resolved_model = str(model).strip() or None
        elif self._resolved_model is None:
            engine_model = getattr(engine, "model", None)
            self._resolved_model = str(engine_model).strip() if engine_model else self._model_ref
        if model_source:
            self._model_source = model_source
        elif self._model_ref:
            self._model_source = "override"
        engine_model = getattr(engine, "model", None)
        _log.info(
            "summarizer llm: model=%s source=%s agent_model=%s instructions=%s",
            self.model or "(none)",
            self._model_source,
            engine_model or "(none)",
            "override" if self._instructions else "default",
        )

    async def summarize(self, messages: list[dict[str, Any]]) -> str | None:
        engine = self._engine
        fn = (
            getattr(engine, "asummarize_messages", None) or getattr(engine, "summarize_messages", None)
            if engine is not None
            else None
        )
        if not callable(fn):
            if not self._warned_no_engine:
                _log.warning(
                    "summarizer llm has no engine.asummarize_messages; "
                    "older turns will be dropped. Bind the agent engine or set summarizer: drop."
                )
                self._warned_no_engine = True
            return None
        prompt = [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": _text_view(messages)},
        ]
        text: Any
        override = self._resolved_model
        if override and _accepts_kwarg(fn, "model"):
            text = await settle(fn(prompt, model=override))
        else:
            text = await settle(fn(prompt))
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        return stripped or None
