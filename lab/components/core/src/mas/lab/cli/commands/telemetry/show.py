#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``mas-lab telemetry show`` command."""
from __future__ import annotations

import click


@click.command("show")
@click.argument("trace_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--filter", "-f", "event_filter",
    default=None,
    help="Filter events by kind prefix (e.g. 'tool_call', 'memory_', 'llm_call').",
)
@click.option(
    "--format", "out_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--limit", "-n",
    type=int,
    default=None,
    help="Show at most N events.",
)
def show_cmd(
    trace_file: str,
    event_filter: str | None,
    out_format: str,
    limit: int | None,
) -> None:
    """Show a summary and events from a TRACE_FILE (events.jsonl).

    Without --filter, prints a session summary (agent count, turn count,
    duration, tool calls, memory accesses).

    With --filter, prints matching events.

    \b
    Examples:
        mas-lab telemetry show logs/events.jsonl
        mas-lab telemetry show logs/events.jsonl --filter tool_call
        mas-lab telemetry show logs/events.jsonl --filter memory_ --format json
    """
    import json as _json

    events = []
    with open(trace_file) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue

    if not events:
        click.echo("No events found.")
        return

    # Apply filter
    if event_filter:
        events = [
            e for e in events
            if (e.get("kind", "") or e.get("event", "")).startswith(event_filter)
        ]

    if limit is not None:
        events = events[:limit]

    if out_format == "json":
        for e in events:
            click.echo(_json.dumps(e, default=str))
        return

    # Text output: summary if no filter, event list otherwise
    if not event_filter:
        # Session summary
        agents = set()
        kinds: dict[str, int] = {}
        first_ts = None
        last_ts = None
        for e in events:
            agent = e.get("agent_id") or e.get("agent")
            if agent:
                agents.add(agent)
            kind = e.get("kind") or e.get("event") or "unknown"
            kinds[kind] = kinds.get(kind, 0) + 1
            ts = e.get("timestamp")
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

        click.echo(f"Trace: {trace_file}")
        click.echo(f"Events: {len(events)}")
        if agents:
            click.echo(f"Agents: {', '.join(sorted(agents))}")
        if first_ts and last_ts:
            click.echo(f"Time range: {first_ts} → {last_ts}")
        click.echo()
        click.echo("Event breakdown:")
        for kind, count in sorted(kinds.items(), key=lambda x: -x[1]):
            click.echo(f"  {kind:40s} {count:>5d}")
    else:
        for e in events:
            kind = e.get("kind") or e.get("event") or "?"
            agent = e.get("agent_id") or e.get("agent") or ""
            ts = e.get("timestamp", "")
            # Compact one-liner per event
            detail_parts = []
            for key in ("tool", "query", "args", "dp_state", "results_count", "score"):
                if key in e:
                    val = e[key]
                    if isinstance(val, dict):
                        val = _json.dumps(val, default=str)
                    detail_parts.append(f"{key}={val}")
            detail = "  ".join(detail_parts) if detail_parts else ""
            click.echo(f"[{ts}] {kind:30s} agent={agent:20s} {detail}")

