#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

"""Event trace analysis helpers."""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Internal analysis
# ---------------------------------------------------------------------------


def _parse_llm_content(raw: str) -> str | None:
    """Extract human-readable content from an LLM response string.

    Handles three cases:
    * Plain prose text → returned as-is.
    * JSON block (``{"type": "tool", ...}``) → skipped (tool call frame).
    * JSON block (``{"type": "next_step"|"final"|"answer", "content": "..."}``
      or similar) → inner ``content`` field extracted.
    """
    raw = raw.strip()
    if not raw:
        return None

    # Try to unwrap ```json ... ``` fence
    body = raw
    if raw.startswith("```json"):
        body = raw[7:].strip()
        if body.endswith("```"):
            body = body[:-3].strip()

    if body.startswith("{") or body.startswith("["):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            # Python repr format (single quotes) — check for known skip patterns
            if any(f"'type': '{t}'" in body for t in ("tool", "plan")):
                return None
            # Check standard JSON format strings too
            if any(f'"type": "{t}"' in body for t in ("tool", "plan")):
                return None
        else:
            t = parsed.get("type", "")
            if t in ("tool", "plan"):
                return None  # pure tool call / planning frame — skip
            # next_step / final / answer / reasoning / etc. — extract content
            content = parsed.get("content") or parsed.get("answer") or parsed.get("reasoning", "")
            return str(content).strip() if content else None
        # Could not parse or identify — fall through to treat as prose

    # Plain prose — return if it looks like a real output (not planning noise)
    _SKIP_PREFIXES = ("Internal Step", "Step ", "Plan generated", "Proceeding to step")
    if any(raw.startswith(p) for p in _SKIP_PREFIXES):
        return None
    return raw if len(raw.split()) > 3 else None


def _extract_agent_output(events: list[dict], agent_id: str, ts_start: float, ts_end: float) -> str:
    """Return the last LLM output (``output``, or ``response.content``) from *agent_id* in the delegation window."""
    best = ""
    best_ts = 0.0
    for e in events:
        if e.get("agent_id") != agent_id:
            continue
        if e.get("kind") != "llm_call_end":
            continue
        ts = e.get("timestamp", 0.0)
        if ts <= ts_start or (ts_end > 0 and ts > ts_end):
            continue
        if ts >= best_ts:
            resp = e.get("response", {})
            raw = e.get("output") or (resp.get("content", "") if isinstance(resp, dict) else str(resp))
            content = _parse_llm_content(raw if isinstance(raw, str) else str(raw))
            if content:
                best = content
                best_ts = ts
    return best


def _extract_delegations(events: list[dict]) -> list[dict]:
    """Extract ordered delegation pairs (routing + routing_result) from events,
    enriched with the target agent's output text."""
    routing_by_cid: dict[str, dict] = {}
    result_by_cid: dict[str, dict] = {}

    for e in events:
        kind = e.get("kind", "")
        cid = e.get("correlation_id") or e.get("request_id") or ""
        if kind == "routing":
            routing_by_cid[cid] = e
        elif kind == "routing_result":
            result_by_cid[cid] = e

    # Merge pairs ordered by the routing timestamp
    pairs: list[dict] = []
    for cid, route_evt in routing_by_cid.items():
        result_evt = result_by_cid.get(cid, {})
        ts_start = route_evt.get("timestamp", 0)
        ts_end = result_evt.get("timestamp", 0)
        target = route_evt.get("target_agent_id", "?")
        output = _extract_agent_output(events, target, ts_start, ts_end)
        pairs.append(
            {
                "source": route_evt.get("source_agent_id", "?"),
                "target": target,
                "task": route_evt.get("task", ""),
                "output": output,
                "status": result_evt.get("status", "unknown"),
                "correlation_id": cid,
                "ts_start": ts_start,
                "ts_end": ts_end,
            }
        )

    pairs.sort(key=lambda p: p["ts_start"])
    return pairs


def _extract_user_frame(events: list[dict]) -> dict | None:
    """Return a synthetic User→entry-agent delegation from execution_start/end.

    Looks for the *first* ``execution_start`` event that carries an ``input``
    field (the user prompt) and the matching ``execution_end`` whose context
    marks ``is_entry_agent: True``.  Returns ``None`` when neither is present.
    """
    # --- find entry-agent execution_start (first one with an input field) ---
    entry_start: dict | None = None
    for e in events:
        if e.get("kind") == "execution_start" and e.get("input"):
            ctx = e.get("context", {})
            if isinstance(ctx, str):
                try:
                    import ast

                    ctx = ast.literal_eval(ctx)
                except Exception:
                    ctx = {}
            # Prefer an explicit is_entry_agent flag, but fall back to the
            # very first execution_start with input as a sensible default.
            if ctx.get("is_entry_agent", False) or entry_start is None:
                entry_start = e
            if ctx.get("is_entry_agent", False):
                break  # definitive match found

    if entry_start is None:
        return None

    entry_agent = entry_start.get("agent_id", "?")
    prompt = entry_start.get("input", "")
    ts_start = float(entry_start.get("timestamp", 0))

    # --- find matching execution_end for the entry agent ---
    final_output = ""
    ts_end = 0.0
    for e in reversed(events):
        if e.get("kind") != "execution_end":
            continue
        if e.get("agent_id") != entry_agent:
            continue
        ctx = e.get("context", {})
        if isinstance(ctx, str):
            try:
                import ast

                ctx = ast.literal_eval(ctx)
            except Exception:
                ctx = {}
        if ctx.get("is_entry_agent", False) or True:  # take any end for this agent
            final_output = e.get("output", "")
            ts_end = float(e.get("timestamp", 0))
            break

    return {
        "source": "User",
        "target": entry_agent,
        "task": prompt,
        "output": final_output,
        "status": "success",
        "correlation_id": "user",
        "ts_start": ts_start,
        "ts_end": ts_end,
    }


def _extract_agent_order(events: list[dict]) -> list[str]:
    """Return agents in first-appearance order (for Mermaid participant list)."""
    seen: list[str] = []
    for e in events:
        for key in ("source_agent_id", "target_agent_id", "agent_id"):
            aid = e.get(key)
            if aid and aid not in seen:
                seen.append(aid)
    return seen


def _short_task(task: str, max_chars: int = 80) -> str:
    """Truncate and sanitise a task string for inline diagram labels."""
    import re

    if not task:
        return ""
    # Collapse whitespace / strip Markdown headers and bold/italic markers
    text = task.strip()
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)  # ## headers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)  # **bold** / *italic*
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)  # __bold__
    text = re.sub(r"\s+", " ", text).strip()
    # keep first sentence or first N chars
    first_sentence = text.split(".")[0].strip()
    label = first_sentence[:max_chars]
    if len(first_sentence) > max_chars:
        label += "…"
    # escape Mermaid special chars
    label = label.replace('"', "'").replace(":", " -").replace("\n", " ").replace("#", "")
    return label
