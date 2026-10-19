#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Man-page catalog for ``mas-lab`` commands.

Each leaf command has a markdown page in the **owning library** (or ``lab/docs/cli``
for core). The published site index is only pointers: ``docs/cli/mas-lab.md``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManPage:
    command: str
    path: str
    summary: str
    group: str


def slug(command: str) -> str:
    parts = command.split()
    if parts and parts[0] == "mas-lab":
        parts = parts[1:]
    return "mas-lab-" + "-".join(parts) if parts else "mas-lab"


# Owning-library paths. Core mas-lab commands live under lab/docs/cli/.
CLI_MAN_PAGES: tuple[ManPage, ...] = (
    # --- library-telemetry ---
    ManPage("mas-lab telemetry", "library-telemetry/docs/cli/index.md", "Convert, verify, push, and dump OTel spans", "telemetry"),
    ManPage("mas-lab telemetry convert", "library-telemetry/docs/cli/convert.md", "events.jsonl → otel_sdk_spans.jsonl", "telemetry"),
    ManPage("mas-lab telemetry verify", "library-telemetry/docs/cli/verify.md", "Structural + SpanSpec verification", "telemetry"),
    ManPage("mas-lab telemetry inspect", "library-telemetry/docs/cli/inspect.md", "Span-file summary without validation", "telemetry"),
    ManPage("mas-lab telemetry push", "library-telemetry/docs/cli/push.md", "Serialize spans to an OTLP collector", "telemetry"),
    ManPage("mas-lab telemetry dump", "library-telemetry/docs/cli/dump.md", "Read spans back from ClickHouse", "telemetry"),
    ManPage("mas-lab telemetry compare", "library-telemetry/docs/cli/compare.md", "Structural parity of two span files", "telemetry"),
    ManPage("mas-lab telemetry kinds", "library-telemetry/docs/cli/kinds.md", "List native event kinds with a span handler", "telemetry"),
    ManPage("mas-lab telemetry list-apps", "library-telemetry/docs/cli/list-apps.md", "List apps in ClickHouse otel_traces", "telemetry"),
    # --- library-kg ---
    ManPage("mas-lab kg", "library-kg/docs/cli/index.md", "Normalize, validate, push, and dump knowledge graphs", "kg"),
    ManPage("mas-lab kg normalize", "library-kg/docs/cli/normalize.md", "Native events or OTel spans → kg.json", "kg"),
    ManPage("mas-lab kg validate", "library-kg/docs/cli/validate.md", "Validate a KG document", "kg"),
    ManPage("mas-lab kg verify", "library-kg/docs/cli/verify.md", "Verify native events.jsonl", "kg"),
    ManPage("mas-lab kg otel-to-events", "library-kg/docs/cli/otel-to-events.md", "OTel spans → clickhouse-shaped JSONL", "kg"),
    ManPage("mas-lab kg otel-to-kg", "library-kg/docs/cli/otel-to-kg.md", "OTel spans → kg.json", "kg"),
    ManPage("mas-lab kg push", "library-kg/docs/cli/push.md", "Serialize a KG to Neo4j", "kg"),
    ManPage("mas-lab kg neo4j-push", "library-kg/docs/cli/neo4j-push.md", "Alias of kg push", "kg"),
    ManPage("mas-lab kg dump", "library-kg/docs/cli/dump.md", "Deserialize a KG from Neo4j", "kg"),
    # --- library-eval ---
    ManPage("mas-lab eval", "library-eval/docs/cli/index.md", "Score traces with MCE LLM-as-judge metrics", "eval"),
    # --- lab core ---
    ManPage("mas-lab", "lab/docs/cli/index.md", "MAS Lab CLI", "lab"),
    ManPage("mas-lab init", "lab/docs/cli/init.md", "Bootstrap user config and optional LLM infra", "lab"),
    ManPage("mas-lab config", "lab/docs/cli/config.md", "Print resolved data and cache paths", "lab"),
    ManPage("mas-lab check", "lab/docs/cli/check.md", "Validate a MAS configuration", "lab"),
    ManPage("mas-lab check-config", "lab/docs/cli/check-config.md", "Validate workspace / experiment config", "lab"),
    ManPage("mas-lab validate", "lab/docs/cli/validate.md", "Validate YAML manifests", "lab"),
    ManPage("mas-lab serve", "lab/docs/cli/serve.md", "Start the benchmark UI server", "lab"),
    ManPage("mas-lab eval-output", "lab/docs/cli/eval-output.md", "Score MAS output quality (MCE)", "lab"),
    ManPage("mas-lab telemetry show", "lab/docs/cli/telemetry-show.md", "Summarize a native events.jsonl file", "lab"),
    ManPage("mas-lab telemetry apps", "lab/docs/cli/telemetry-apps.md", "List apps in native telemetry stores", "lab"),
    ManPage("mas-lab telemetry apps list", "lab/docs/cli/telemetry-apps-list.md", "List apps in ClickHouse otel_traces", "lab"),
    ManPage("mas-lab telemetry sessions", "lab/docs/cli/telemetry-sessions.md", "List native telemetry sessions", "lab"),
    ManPage("mas-lab telemetry sessions list", "lab/docs/cli/telemetry-sessions-list.md", "List native telemetry sessions", "lab"),
    ManPage("mas-lab telemetry traces", "lab/docs/cli/telemetry-traces.md", "List native telemetry traces", "lab"),
    ManPage("mas-lab telemetry traces list", "lab/docs/cli/telemetry-traces-list.md", "List native telemetry traces", "lab"),
    ManPage("mas-lab otel", "library-telemetry/docs/cli/otel.md", "Alias of mas-lab telemetry", "telemetry"),

    ManPage("mas-lab plot", "lab/docs/cli/plot.md", "Trajectory and communication diagrams", "plot"),
    ManPage("mas-lab plot trajectory", "lab/docs/cli/plot-trajectory.md", "Delegation-flow diagram", "plot"),
    ManPage("mas-lab plot multilevel-trajectory", "lab/docs/cli/plot-multilevel-trajectory.md", "Multilevel swimlane diagram", "plot"),
    ManPage("mas-lab plot communication-flow", "lab/docs/cli/plot-communication-flow.md", "Agent-to-agent routing graph", "plot"),
    ManPage("mas-lab plot kg", "lab/docs/cli/plot-kg.md", "Interactive knowledge-graph HTML", "plot"),
    ManPage("mas-lab plot message-graph", "lab/docs/cli/plot-message-graph.md", "Message swimlane from kg.json", "plot"),
    ManPage("mas-lab plot context-provenance", "lab/docs/cli/plot-context-provenance.md", "Context-provenance diagram from kg.json", "plot"),
    ManPage("mas-lab plot list", "lab/docs/cli/plot-list.md", "Discover generated plot artefacts", "plot"),
    ManPage("mas-lab run", "lab/docs/cli/run.md", "Run processors and pipeline steps", "run"),
    ManPage("mas-lab run processor", "lab/docs/cli/run-processor.md", "Run one processor", "run"),
    ManPage("mas-lab run pipeline", "lab/docs/cli/run-pipeline.md", "Pipeline steps without a full benchmark", "run"),
    ManPage("mas-lab run pipeline step", "lab/docs/cli/run-pipeline-step.md", "Run named pipeline steps on existing output", "run"),
    ManPage("mas-lab pipe", "lab/docs/cli/pipe.md", "Processor pipeline (GStreamer-style)", "pipe"),
    ManPage("mas-lab pipe run", "lab/docs/cli/pipe-run.md", "Execute an inline or YAML processor pipeline", "pipe"),
    ManPage("mas-lab pipe list", "lab/docs/cli/pipe-list.md", "List registered processor elements", "pipe"),
    ManPage("mas-lab pipe schema", "lab/docs/cli/pipe-schema.md", "Export element registry as JSON schema", "pipe"),
    ManPage("mas-lab pipe validate", "lab/docs/cli/pipe-validate.md", "Validate a processor pipeline", "pipe"),
    ManPage("mas-lab benchmark", "lab/docs/cli/benchmark.md", "Run, list, show, and manage experiments", "benchmark"),
    ManPage("mas-lab benchmark run", "lab/docs/cli/benchmark-run.md", "Execute an experiment YAML", "benchmark"),
    ManPage("mas-lab benchmark list", "lab/docs/cli/benchmark-list.md", "List benchmark runs", "benchmark"),
    ManPage("mas-lab benchmark show", "lab/docs/cli/benchmark-show.md", "Show a benchmark run", "benchmark"),
    ManPage("mas-lab benchmark update", "lab/docs/cli/benchmark-update.md", "Update benchmark metadata", "benchmark"),
    ManPage("mas-lab benchmark rename", "lab/docs/cli/benchmark-rename.md", "Rename a benchmark", "benchmark"),
    ManPage("mas-lab benchmark migrate", "lab/docs/cli/benchmark-migrate.md", "Migrate benchmark output layout", "benchmark"),
    ManPage("mas-lab benchmark analyze", "lab/docs/cli/benchmark-analyze.md", "Regenerate plots from existing output", "benchmark"),
    ManPage("mas-lab benchmark export", "lab/docs/cli/benchmark-export.md", "Export a benchmark archive", "benchmark"),
    ManPage("mas-lab benchmark import", "lab/docs/cli/benchmark-import.md", "Import a benchmark archive", "benchmark"),
    ManPage("mas-lab benchmark follow", "lab/docs/cli/benchmark-follow.md", "Follow progress of an active run", "benchmark"),
    ManPage("mas-lab benchmark clean", "lab/docs/cli/benchmark-clean.md", "Remove benchmark run data", "benchmark"),
    ManPage("mas-lab benchmark step-info", "lab/docs/cli/benchmark-step-info.md", "Show registered pipeline step types", "benchmark"),
    ManPage("mas-lab benchmark artifact", "lab/docs/cli/benchmark-artifact.md", "List and show experiment artifacts", "benchmark"),
    ManPage("mas-lab benchmark artifact list", "lab/docs/cli/benchmark-artifact-list.md", "List artifacts for a run", "benchmark"),
    ManPage("mas-lab benchmark artifact show", "lab/docs/cli/benchmark-artifact-show.md", "Show one artifact by id", "benchmark"),
    ManPage("mas-lab benchmark step", "lab/docs/cli/benchmark-step.md", "Inspect or restart benchmark steps", "benchmark"),
    ManPage("mas-lab benchmark step list", "lab/docs/cli/benchmark-step-list.md", "List steps in a benchmark", "benchmark"),
    ManPage("mas-lab benchmark step show", "lab/docs/cli/benchmark-step-show.md", "Show one step", "benchmark"),
    ManPage("mas-lab benchmark step restart", "lab/docs/cli/benchmark-step-restart.md", "Restart one step", "benchmark"),
    ManPage("mas-lab benchmark pipeline", "lab/docs/cli/benchmark-pipeline.md", "Standalone pipeline YAML", "benchmark"),
    ManPage("mas-lab benchmark pipeline run", "lab/docs/cli/benchmark-pipeline-run.md", "Execute a pipeline YAML", "benchmark"),
    ManPage("mas-lab benchmark pipeline plan", "lab/docs/cli/benchmark-pipeline-plan.md", "Show execution plan without running", "benchmark"),
    ManPage("mas-lab benchmark pipeline show", "lab/docs/cli/benchmark-pipeline-show.md", "Show pipeline structure", "benchmark"),
    ManPage("mas-lab benchmark pipeline validate", "lab/docs/cli/benchmark-pipeline-validate.md", "Validate pipeline YAML", "benchmark"),
    ManPage("mas-lab benchmark daemon", "lab/docs/cli/benchmark-daemon.md", "Background UI server", "benchmark"),
    ManPage("mas-lab benchmark daemon start", "lab/docs/cli/benchmark-daemon-start.md", "Start the UI daemon", "benchmark"),
    ManPage("mas-lab benchmark daemon stop", "lab/docs/cli/benchmark-daemon-stop.md", "Stop the UI daemon", "benchmark"),
    ManPage("mas-lab benchmark daemon status", "lab/docs/cli/benchmark-daemon-status.md", "Show UI daemon status", "benchmark"),
    ManPage("mas-lab control", "lab/docs/cli/control.md", "Controller daemon lifecycle", "control"),
    ManPage("mas-lab control start", "lab/docs/cli/control-start.md", "Start the controller daemon", "control"),
    ManPage("mas-lab control stop", "lab/docs/cli/control-stop.md", "Stop the controller daemon", "control"),
    ManPage("mas-lab control status", "lab/docs/cli/control-status.md", "Show controller status", "control"),
    ManPage("mas-lab worker", "lab/docs/cli/worker.md", "Background workers", "worker"),
    ManPage("mas-lab worker list", "lab/docs/cli/worker-list.md", "List workers", "worker"),
    ManPage("mas-lab worker show", "lab/docs/cli/worker-show.md", "Show one worker", "worker"),
    ManPage("mas-lab worker cancel", "lab/docs/cli/worker-cancel.md", "Cancel a worker", "worker"),
    ManPage("mas-lab worker follow", "lab/docs/cli/worker-follow.md", "Follow worker output", "worker"),
    ManPage("mas-lab demo-ui", "lab/docs/cli/demo-ui.md", "Offline live-dashboard export", "demo-ui"),
    ManPage("mas-lab demo-ui export", "lab/docs/cli/demo-ui-export.md", "Write a static site from events.jsonl or a ui-trace", "demo-ui"),
    ManPage("mas-lab demo-ui replay", "lab/docs/cli/demo-ui-replay.md", "Alias of mas-lab replay", "demo-ui"),
    ManPage("mas-lab replay", "lab/docs/cli/replay.md", "Replay native events.jsonl or a ui-trace into the live dashboard", "demo-ui"),
)


def pages_by_group() -> dict[str, list[ManPage]]:
    out: dict[str, list[ManPage]] = {}
    for page in CLI_MAN_PAGES:
        out.setdefault(page.group, []).append(page)
    return out


def render_man(
    page: ManPage,
    *,
    synopsis: str,
    description: str,
    options: str = "",
    see_also: tuple[str, ...] = (),
    pipeline: str = "",
) -> str:
    title = slug(page.command)
    opts = options.strip() or (
        f"See `{page.command} --help` for the current flag list "
        "(Click help is the source of truth)."
    )
    see = "\n".join(f"- `{name}`" for name in see_also) or "- `mas-lab --help`"
    pipe = f"\n## PIPELINE\n\n`{pipeline}`\n" if pipeline else ""
    return (
        f"<!--\n  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates\n"
        f"  SPDX-License-Identifier: Apache-2.0\n-->\n"
        f"# {title}(1)\n\n"
        f"## NAME\n\n`{page.command}` — {page.summary}\n\n"
        f"## SYNOPSIS\n\n```text\n{synopsis}\n```\n\n"
        f"## DESCRIPTION\n\n{description.strip()}\n\n"
        f"## OPTIONS\n\n{opts}\n"
        f"{pipe}\n"
        f"## SEE ALSO\n\n{see}\n"
    )


def click_options_markdown(cmd: object) -> str:
    """Render a command's Click params as an OPTIONS table."""
    try:
        import click
    except ImportError:  # pragma: no cover
        return "See `--help` for the current flag list."
    rows: list[str] = []
    for param in getattr(cmd, "params", None) or []:
        if isinstance(param, click.Argument):
            names = f"`{(param.metavar or param.name or 'ARG').upper()}`"
            rows.append(f"| {names} | Positional argument. |")
            continue
        if not isinstance(param, click.Option):
            continue
        flags = ", ".join(f"`{opt}`" for opt in (*param.opts, *param.secondary_opts))
        help_text = (param.help or "").replace("|", "\\|").replace("\n", " ").strip() or "—"
        rows.append(f"| {flags} | {help_text} |")
    if not rows:
        return "This command takes no extra flags. See `--help` for inherited group options."
    return "| Flag | Meaning |\n|------|------|\n" + "\n".join(rows)


def walk_click_commands(group: object, prefix: str = "mas-lab") -> list[tuple[str, object]]:
    """Depth-first `(command string, click object)` for *group* and its leaves."""
    out: list[tuple[str, object]] = [(prefix, group)]
    commands = getattr(group, "commands", None) or {}
    for name, cmd in sorted(commands.items()):
        path = f"{prefix} {name}"
        if getattr(cmd, "commands", None):
            out.extend(walk_click_commands(cmd, path))
        else:
            out.append((path, cmd))
    return out


def replace_options_section(text: str, markdown: str) -> str:
    import re

    patched, n = re.subn(
        r"(## OPTIONS\n\n)(.*?)(\n## )",
        lambda m: m.group(1) + markdown.strip() + "\n" + m.group(3),
        text,
        count=1,
        flags=re.S,
    )
    if n:
        return patched
    if "## OPTIONS" in text:
        return text
    if "## SEE ALSO" in text:
        return text.replace(
            "## SEE ALSO",
            f"## OPTIONS\n\n{markdown.strip()}\n\n## SEE ALSO",
            1,
        )
    return text + f"\n## OPTIONS\n\n{markdown.strip()}\n"


def sync_man_options(root: object) -> int:
    """Fill OPTIONS tables from the live Click tree. Returns pages patched."""
    from pathlib import Path

    dest_root = Path(str(root))
    try:
        from mas.lab.cli import app
    except Exception:
        return 0
    by_cmd = {page.command: page for page in CLI_MAN_PAGES}
    patched = 0
    for command, cmd in walk_click_commands(app):
        page = by_cmd.get(command)
        if page is None:
            continue
        path = dest_root / page.path
        if not path.is_file():
            continue
        table = click_options_markdown(cmd)
        original = path.read_text(encoding="utf-8")
        updated = replace_options_section(original, table)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            patched += 1
    return patched
