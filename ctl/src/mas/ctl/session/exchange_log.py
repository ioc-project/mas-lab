#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Pretty-print structured ExchangeRecord values for CLI --trace.

The driver emits typed fields. This module is a view: layout and color happen
here, never on the interchange.

Color semantics for CLI trace output:
  - Agent: cyan (#0e7490)
  - LLM: orange (#ea580c)
  - Tool: green (#16a34a)
  - Processing: slate (#64748b)
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any

from mas.runtime.boundary.obs.exchange_plugin import ExchangePlugin
from mas.runtime.driver.driver import ExchangeKind, ExchangeRecord
from mas.runtime.engine.exchange_preview import (
    format_llm_messages,
    format_llm_response,
    format_tool_invoke,
)

_COLORS = {
    "agent": "\033[36m",  # cyan
    "llm": "\033[33m",  # orange/yellow
    "tool": "\033[32m",  # green
    "processing": "\033[90m",  # slate/gray
    "user": "\033[37m",  # white (user input)
    "reset": "\033[0m",  # reset
}

_KIND_EDGE: dict[ExchangeKind, tuple[str, str]] = {
    "user_in": ("USER", "AGENT"),
    "user_out": ("AGENT", "USER"),
    "llm_request": ("AGENT", "LLM"),
    "llm_response": ("LLM", "AGENT"),
    "tool_call": ("AGENT", "TOOL"),
    "tool_result": ("TOOL", "AGENT"),
    "gov_block": ("AGENT", "GOV"),
}

_ENTITY_COLOR = {"AGENT": "agent", "USER": "user", "LLM": "llm", "TOOL": "tool", "SKILL": "tool", "GOV": "processing"}


def _colorize(text: str, color_key: str) -> str:
    """Wrap already-chosen tokens in ANSI. Not a parser."""
    return f"{_COLORS.get(color_key, '')}{text}{_COLORS['reset']}"


@dataclass(frozen=True)
class TraceFormatOptions:
    timestamps: bool = False
    engine_io: bool = False
    summary_only: bool = False
    turn_start_mono: float = 0.0
    color: bool = False  # Use ANSI color codes for visual separation
    agent_name: str = "agent"  # Identity name for agent
    tool_name: str | None = None  # Unused; tool identity comes from the record
    llm_name: str = "LLM"  # Identity name for LLM


def print_exchange(
    ex: ExchangeRecord,
    *,
    err: object = sys.stderr,
    agent_id: str = "agent",
    fmt: TraceFormatOptions | None = None,
) -> None:
    """Write one exchange block (streaming trace)."""
    write = getattr(err, "write", None)
    flush = getattr(err, "flush", None)
    if not callable(write):
        return
    block = format_exchange(agent_id, ex, fmt=fmt)
    write(block)
    if block and not block.endswith("\n"):
        write("\n")
    if callable(flush):
        flush()


def format_exchange(
    agent_id: str,
    ex: ExchangeRecord,
    *,
    fmt: TraceFormatOptions | None = None,
) -> str:
    opts = fmt or TraceFormatOptions()
    if opts.summary_only:
        return _format_summary(agent_id, ex, opts)
    return _format_dump(agent_id, ex, opts)


class CliTraceExchangePlugin(ExchangePlugin):
    """ExchangePlugin driving mas-ctl's own --trace/--verbose stdout/log output.

    One persistent instance per SessionController, subscribed exactly once
    via KernelDriver.subscribe_exchange() (see
    SessionController._setup_exchange_tracing()) instead of being rebuilt
    and reassigned to a single driver.on_exchange callback every turn —
    per-turn state (trace/verbose toggles, turn_start_mono baseline) is
    updated in place via configure(), so subscribing only ever happens once
    per controller/driver pair, and other subscribers (e.g. a chat-UI
    plugin) are never silently discarded.
    """

    def __init__(self, *, err=sys.stderr) -> None:
        self.enabled = False
        self.trace = False
        self.verbose = 0
        self.agent_id = "agent"
        self.fmt = TraceFormatOptions()
        self._err = err
        self._logger = logging.getLogger("mas.runtime")

    def configure(
        self,
        *,
        trace: bool,
        verbose: int,
        agent_id: str,
        fmt: TraceFormatOptions,
    ) -> None:
        self.enabled = trace or verbose >= 1
        self.trace = trace
        self.verbose = verbose
        self.agent_id = agent_id
        self.fmt = fmt

    def on_exchange(self, record: ExchangeRecord) -> None:
        # Internals stay off the default console so demos and --trace stay
        # the conversation + hops. Pass -v (same as summarization logs).
        if self.verbose >= 1:
            _maybe_note_cache_hit(record, err=self._err)
        if not self.enabled:
            return
        if self.trace:
            print_exchange(record, err=self._err, agent_id=self.agent_id, fmt=self.fmt)
        elif self.verbose >= 1:
            formatted = format_exchange(self.agent_id, record, fmt=self.fmt).strip()
            for line in formatted.splitlines():
                self._logger.info("[%s] %s", self.agent_id, line)


def _format_summary(agent_id: str, ex: ExchangeRecord, opts: TraceFormatOptions) -> str:
    parts: list[str] = []
    if ex.ts_mono and opts.turn_start_mono:
        offset = ex.ts_mono - opts.turn_start_mono
        ts_str = f"+{offset:.3f}s"
        if opts.timestamps and ex.ts_wall:
            ts_str += f" [{ex.ts_wall}]"
        parts.append(_tone(ts_str, "processing", opts.color))

    agent_name = agent_id or opts.agent_name
    llm_name = ex.model or opts.llm_name
    parts.append(
        _header_with_identities(
            ex,
            agent_name=agent_name,
            llm_name=llm_name,
            use_colors=opts.color,
        )
    )

    if ex.kind == "user_out":
        body = ex.text.strip()
        line = " ".join(parts)
        if not body:
            return line
        body_fmt = _tone(body, "user", opts.color)
        if "\n" in body:
            return f"{line}\n{body_fmt}"
        return f"{line} {body_fmt}"

    preview = _summary_preview(ex)
    if preview:
        parts.append(_tone(preview, "user", opts.color))
    return " ".join(parts)


def _format_dump(agent_id: str, ex: ExchangeRecord, opts: TraceFormatOptions) -> str:
    lines: list[str] = []
    header = _dump_header(ex, agent_id, opts)
    if opts.timestamps and ex.ts_wall:
        delta = ""
        if ex.ts_mono and opts.turn_start_mono:
            delta = f" (+{ex.ts_mono - opts.turn_start_mono:.3f}s)"
        lines.append(f"{header}  {ex.ts_wall}{delta}")
    else:
        lines.append(header)
    meta = _metadata_line(ex)
    if meta:
        lines.append(f"  {meta}")
    if opts.engine_io and ex.engine_raw.strip():
        lines.append("  engine:")
        for line in ex.engine_raw.strip().splitlines():
            lines.append(f"    {line}")
    body = _pretty_body(ex)
    if body.strip():
        for line in body.splitlines():
            lines.append(f"  {line}")
    return "\n".join(lines) + "\n"


def _pretty_body(ex: ExchangeRecord) -> str:
    if ex.kind == "llm_request":
        if ex.messages:
            return format_llm_messages(ex.messages, tools=ex.tools, tools_note=ex.tools_note)
        return ex.text
    if ex.kind == "tool_call":
        return format_tool_invoke(ex.tool_name or "tool", ex.tool_arguments)
    if ex.kind == "llm_response":
        return format_llm_response(
            text=ex.text,
            next_step=ex.next_step or "",
            tool_name=ex.tool_name or "",
            tool_arguments=ex.tool_arguments,
            response_kind=ex.response_kind or "MODEL_TEXT",
        )
    return ex.text


def _summary_preview(ex: ExchangeRecord) -> str:
    if ex.kind == "llm_request":
        return _truncate(_last_non_system_content(ex) or ex.text)
    if ex.kind == "llm_response":
        if ex.tool_arguments:
            return _truncate(_compact_json(ex.tool_arguments))
        return _truncate(ex.text)
    if ex.kind == "tool_call":
        return _truncate(_compact_json(ex.tool_arguments or {}))
    return _truncate(ex.text)


def _last_non_system_content(ex: ExchangeRecord) -> str:
    for msg in reversed(ex.messages or []):
        if str(msg.get("role") or "") == "system":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list) and content:
            return _compact_json(content)
    return ""


_CACHE_HIT_NOTE = (
    "note: LLM response served from cache{layer}; "
    "use --no-cache-read or MAS_LLM_CACHE_READ=false to call the provider\n"
)


def _maybe_note_cache_hit(record: ExchangeRecord, *, err=sys.stderr) -> None:
    if record.kind != "llm_response" or record.cache_status != "hit":
        return
    layer = f" (layer={record.cache_layer})" if record.cache_layer else ""
    err.write(_CACHE_HIT_NOTE.format(layer=layer))
    err.flush()


def _metadata_line(ex: ExchangeRecord) -> str:
    parts: list[str] = []
    if ex.correlation_id is not None:
        parts.append(f"correlation_id={ex.correlation_id}")
    if ex.op:
        parts.append(f"op={ex.op}")
    if ex.response_kind:
        parts.append(f"response_kind={ex.response_kind}")
    if ex.finish_reason:
        parts.append(f"finish_reason={ex.finish_reason}")
    if ex.tool_name and ex.kind in {"llm_response", "tool_call", "tool_result"}:
        parts.append(f"tool={ex.tool_name}")
    if ex.kind == "llm_response" and ex.cache_status:
        layer = f" layer={ex.cache_layer}" if ex.cache_layer else ""
        parts.append(f"cache={ex.cache_status}{layer}")
    return " ".join(parts)


def _header_edge(ex: ExchangeRecord) -> tuple[str, str]:
    if ex.kind == "llm_response" and (ex.tool_name or ex.semantics):
        return ("LLM", "TOOL")
    return _KIND_EDGE.get(ex.kind, (ex.kind, ex.kind))


def _semantic_token(semantics: dict[str, Any] | None) -> str | None:
    if not semantics:
        return None
    concept = str(semantics.get("concept") or "").strip()
    if not concept:
        return None
    token = str(semantics.get("subject") or semantics.get("op") or "").strip()
    if token:
        return f"{concept.upper()}[{token}]"
    return concept.upper()


def _tool_label(ex: ExchangeRecord) -> str:
    parts = [f"TOOL[{ex.tool_name}]" if ex.tool_name else "TOOL"]
    semantic = _semantic_token(ex.semantics)
    if semantic:
        parts.append(semantic)
    return " ".join(parts)


def _dump_header(ex: ExchangeRecord, agent_id: str, opts: TraceFormatOptions) -> str:
    inner = _header_with_identities(
        ex,
        agent_name=agent_id or opts.agent_name,
        llm_name=ex.model or opts.llm_name,
        use_colors=False,
    )
    return f"\n── {inner.replace(' -> ', ' → ')} ──"


def _header_with_identities(
    ex: ExchangeRecord,
    *,
    agent_name: str,
    llm_name: str,
    use_colors: bool,
) -> str:
    src, dst = _header_edge(ex)

    def format_entity(entity: str) -> str:
        if entity == "AGENT":
            label = "AGENT" if agent_name == "n/a" else f"AGENT[{agent_name}]"
        elif entity == "USER":
            # A delegated turn's "USER" edge is really the calling agent, not
            # the human user (see KernelDriver.caller_agent_id) -- label it
            # accordingly so a multi-agent trace does not misreport a peer
            # hand-off as user input/output.
            label = f"AGENT[{ex.caller_agent_id}]" if ex.caller_agent_id else "USER"
        elif entity == "LLM":
            label = f"LLM[{llm_name}]" if llm_name else "LLM"
        elif entity == "TOOL":
            label = _tool_label(ex)
        elif entity == "GOV":
            label = f"GOV[{ex.policy_name}]" if ex.policy_name else "GOV"
        else:
            label = entity
        color_key = "tool" if entity == "TOOL" else _ENTITY_COLOR.get(entity, "processing")
        if use_colors:
            return _colorize(label, color_key)
        return label

    return f"{format_entity(src)} -> {format_entity(dst)}"


def _tone(text: str, color_key: str, use_colors: bool) -> str:
    if use_colors:
        return _colorize(text, color_key)
    return text


def _truncate(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _compact_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(obj)
