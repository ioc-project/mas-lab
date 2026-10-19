#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI extension — ``mas-lab control`` and ``mas-lab worker`` command groups."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import click


@click.group("control")
def control_group() -> None:
    """Controller daemon lifecycle (Unix socket + optional HTTP)."""


@control_group.command("start")
@click.option("--port", "-p", type=int, default=9000, show_default=True, help="HTTP port for UI.")
@click.option("--no-http", is_flag=True, help="IPC only — no HTTP server.")
@click.option("--foreground", "-f", is_flag=True, help="Run daemon in foreground (dev).")
def control_start(port: int, no_http: bool, foreground: bool) -> None:
    """Start the MAS Lab controller daemon."""
    from mas.lab.controller.client import ControllerClient, drift_message, start_daemon

    client = ControllerClient()
    if client.is_running():
        click.echo("Controller is already running.")
        drift = client.drift()
        if drift:
            click.echo(f"Warning: {drift_message(drift)}", err=True)
        return
    if foreground:
        import sys

        argv = ["--port", str(port)]
        if no_http:
            argv.append("--no-http")
        from mas.lab.controller.daemon import main

        raise SystemExit(main(argv))
    start_daemon(port=port)
    for _ in range(40):
        if client.is_running():
            click.echo(f"Controller started (HTTP http://127.0.0.1:{port}).")
            return
        time.sleep(0.25)
    click.echo("Controller start initiated — verify with: mas-lab control status")


@control_group.command("stop")
def control_stop() -> None:
    """Stop the controller daemon."""
    from mas.lab.controller.client import stop_daemon

    stop_daemon()
    click.echo("Controller stopped.")


@control_group.command("status")
def control_status() -> None:
    """Show controller daemon status."""
    from mas.lab.controller.client import ControllerClient, drift_message
    from mas.lab.controller import config as cfg

    client = ControllerClient()
    if not client.is_running():
        click.echo(f"Controller: not running (socket {cfg.socket_path()})")
        raise SystemExit(1)
    info = client.call("status")
    pid_path = cfg.pid_path()
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else "?"
    click.echo(f"Controller: running (pid {pid})")
    click.echo(json.dumps(info, indent=2))
    drift = client.drift()
    if drift:
        click.echo(f"Warning: {drift_message(drift)}", err=True)


@click.group("worker")
def worker_group() -> None:
    """List and control background workers."""


@worker_group.command("list")
@click.option("--kind", type=click.Choice(["application", "benchmark", "pipeline"]), default=None)
@click.option("--status", default=None)
def worker_list(kind: str | None, status: str | None) -> None:
    """List workers."""
    from mas.lab.controller.client import ControllerClient

    client = ControllerClient()
    client.ensure_running()
    workers = client.call("list_workers", {"kind": kind, "status": status})
    if not workers:
        click.echo("No workers.")
        return
    for w in workers:
        click.echo(f"{w['id']}\t{w['status']}\t{w.get('command', '')}")


@worker_group.command("show")
@click.argument("worker_id")
def worker_show(worker_id: str) -> None:
    """Show worker details."""
    from mas.lab.controller.client import ControllerClient

    client = ControllerClient()
    client.ensure_running()
    detail = client.call("get_worker", {"worker_id": worker_id})
    if detail is None:
        click.secho(f"Worker not found: {worker_id}", fg="red")
        raise SystemExit(1)
    click.echo(json.dumps(detail, indent=2))


@worker_group.command("cancel")
@click.argument("worker_id")
def worker_cancel(worker_id: str) -> None:
    """Cancel a running worker."""
    from mas.lab.controller.client import ControllerClient

    client = ControllerClient()
    client.ensure_running()
    client.call("cancel_worker", {"worker_id": worker_id})
    click.echo(f"Cancel requested for {worker_id}")


@worker_group.command("follow")
@click.argument("worker_id")
@click.option("--interval", type=float, default=0.5, show_default=True,
              help="Poll interval in seconds (REST poll, same as blocking benchmark run).")
def worker_follow(worker_id: str, interval: float) -> None:
    """Follow worker output until completion (polls daemon, streams stdout/stderr)."""
    from mas.lab.controller.client import follow_worker

    try:
        detail = follow_worker(worker_id, poll=interval, stream=True)
    except RuntimeError as exc:
        click.secho(str(exc), fg="red")
        raise SystemExit(1) from exc

    status = detail.get("status")
    click.echo(f"\nWorker {worker_id}: {status} (exit {detail.get('exit_code')})")
    ok = status == "completed" and (detail.get("exit_code") in (0, None))
    raise SystemExit(0 if ok else 1)


@click.group("demo-ui")
def demo_ui_group() -> None:
    """Offline demo-ui export (same plugins, no backend)."""


@demo_ui_group.command("export")
@click.argument("trace", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("-o", "--output", type=click.Path(path_type=Path), required=True)
@click.option("--layout", default="run-view.yaml", show_default=True)
@click.option(
    "--layouts",
    "layouts_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory to resolve --layout from, e.g. "
        "labs/<app>.lab/demo-ui/layouts for a library-specific layout that "
        "isn't under the top-level demo-ui/layouts/. Defaults to the "
        "top-level demo-ui/layouts/ (same as live app.py's layout_roots() "
        "first entry) when omitted -- unlike the live server, this command "
        "does not search every registered library's own demo-ui/layouts/ "
        "automatically, so a library-specific layout needs this flag."
    ),
)
@click.option(
    "--scenario",
    "scenarios",
    multiple=True,
    metavar="NAME=TRACE",
    help=(
        "Merge-bundle mode for a multi-panel compare layout: repeat once per "
        "panel scenario, e.g. --scenario baseline=infra/.cache/baseline.events.jsonl "
        "--scenario overlay-b=infra/.cache/overlay-b.events.jsonl ... Each panel's own "
        "dataSource.scenario in --layout must match one of these NAMEs. Ignores "
        "the positional TRACE argument when given."
    ),
)
@click.option("--no-build", is_flag=True, help="Reuse demo-ui/dist if present.")
@click.option("--serve", is_flag=True, help="Serve the export and open a browser.")
@click.option("--port", type=int, default=8766, show_default=True)
def demo_ui_export(
    trace: Path | None,
    output: Path,
    layout: str,
    layouts_root: Path | None,
    scenarios: tuple[str, ...],
    no_build: bool,
    serve: bool,
    port: int,
) -> None:
    """Write a static site from events.jsonl (or a trace directory).

    With one or more --scenario NAME=TRACE options, builds one merged bundle
    covering all of them instead -- for a multi-panel compare layout where
    each panel scopes to its own dataSource.scenario (see
    export_merged_trace's docstring for why one merged bundle, rather than
    one per panel, is what the frontend actually needs).
    """
    from mas.lab.controller.ui_stream.export import (
        export_merged_trace,
        export_trace,
        main as export_main,
    )

    if scenarios:
        scenario_traces: dict[str, Path] = {}
        for item in scenarios:
            if "=" not in item:
                raise click.BadParameter(f"expected NAME=TRACE, got {item!r}", param_hint="--scenario")
            name, _, raw_path = item.partition("=")
            name = name.strip()
            trace_path = Path(raw_path.strip())
            if not trace_path.exists():
                raise click.BadParameter(f"no such path: {trace_path}", param_hint="--scenario")
            scenario_traces[name] = trace_path
        dest = export_merged_trace(
            scenario_traces, output, layout_name=layout, layouts_root=layouts_root, build_ui=not no_build
        )
        click.echo(f"Wrote {dest} ({len(scenario_traces)} scenarios merged: {', '.join(scenario_traces)})")
        if serve:
            subprocess.run(
                [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(dest)],
                check=False,
            )
        else:
            click.echo(f"python3 -m http.server {port} --bind 127.0.0.1 --directory {dest}")
        return

    if trace is None:
        raise click.UsageError("TRACE is required unless --scenario is given")

    if serve:
        argv = [str(trace), "-o", str(output), "--layout", layout, "--serve", "--port", str(port)]
        if no_build:
            argv.append("--no-build")
        raise SystemExit(export_main(argv))
    dest = export_trace(trace, output, layout_name=layout, layouts_root=layouts_root, build_ui=not no_build)
    click.echo(f"Wrote {dest}")
    click.echo(f"python3 -m http.server {port} --bind 127.0.0.1 --directory {dest}")


@click.command("replay")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--speed",
    type=float,
    default=None,
    help="Speedup: 1 = recorded gaps, 8 = 8×, 0 = dump. Default is --replay-time 15.",
)
@click.option(
    "--replay-time",
    type=float,
    default=None,
    help="Target wall seconds; sets --speed to duration/time (default 15).",
)
@click.option(
    "--info",
    "show_info",
    is_flag=True,
    help="Print duration, event count, and speedup; do not ingest.",
)
@click.option("--json", "as_json", is_flag=True, help="JSON for --info.")
@click.option(
    "--url",
    default=None,
    help="Ingest URL. Default MAS_DEMO_UI_INGEST_URL or http://127.0.0.1:8090/api/ingest.",
)
@click.option("--scenario", default=None, help="Override the scenario stamp on a single-run replay.")
@click.option("--library", default=None)
@click.option("--experiment", default=None)
@click.option("--overlay", default=None)
@click.option(
    "--ref",
    "ref_json",
    default=None,
    help="Extra JSON object merged into each ingest ref.",
)
@click.option(
    "--only",
    default=None,
    help="Comma-separated scenario names to replay (e.g. baseline,overlay-b).",
)
@click.option(
    "--align",
    type=click.Choice(["time", "gap"]),
    default="time",
    show_default=True,
    help="time = one shared clock across files; gap = each file on its own gaps.",
)
@click.option(
    "--extra",
    "extras",
    multiple=True,
    help="Sidecar file as SCENARIO=PATH (JSON snapshot or detector dump). Repeatable.",
)
@click.option("--wait/--detach", default=True, show_default=True, help="Wait until the replay job finishes.")
@click.option(
    "--recompute",
    is_flag=True,
    help="ui-trace only: replay runtime and re-fold missing plot artefacts. Does not run the MAS or experiment pipelines.",
)
@click.option(
    "--trees",
    default=None,
    help="ui-trace only: comma-separated trees to play (runtime,pipeline).",
)
@click.option(
    "--record",
    type=click.Path(path_type=Path),
    default=None,
    help="Write a ui-trace directory (runtime.jsonl + pipeline.jsonl) after the job.",
)
def replay_cmd(
    target: Path,
    speed: float | None,
    replay_time: float | None,
    show_info: bool,
    as_json: bool,
    url: str | None,
    scenario: str | None,
    library: str | None,
    experiment: str | None,
    overlay: str | None,
    ref_json: str | None,
    only: str | None,
    align: str,
    extras: tuple[str, ...],
    wait: bool,
    recompute: bool,
    trees: str | None,
    record: Path | None,
) -> None:
    """Replay native events.jsonl (default) or a recorded ui-trace directory.

    Default input is native ``events.jsonl``. A file is that jsonl. A directory
    uses ``infra/.cache/*.events.jsonl``, else ``**/events.jsonl``. A ui-trace
    dir (manifest.json + runtime.jsonl) wins over nested events.jsonl.

    Chat, topology, and series are live-folded from those records.
    Experiment pipelines and the MAS are not executed. ``--recompute`` only
    re-folds missing ui-trace plot artefacts.

    ``--align time`` (default for several files) shares one clock.
    """
    extra: dict[str, object] = {}
    if ref_json:
        parsed = json.loads(ref_json)
        if not isinstance(parsed, dict):
            raise click.BadParameter("--ref must be a JSON object")
        extra = parsed
    only_names = tuple(part.strip() for part in (only or "").split(",") if part.strip())
    extra_files: list[tuple[str, Path]] = []
    for item in extras:
        if "=" not in item:
            raise click.BadParameter("--extra needs SCENARIO=PATH")
        name, path = item.split("=", 1)
        extra_files.append((name.strip(), Path(path)))
    from mas.lab.controller.ui_stream.replay_cli import describe_target, format_trace_info, replay_target

    if speed is not None and replay_time is not None:
        raise click.UsageError("use --speed or --replay-time, not both")
    if show_info:
        try:
            rows = describe_target(target, only=only_names, replay_time=replay_time)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc
        if as_json:
            click.echo(json.dumps(rows, indent=2, default=str))
        else:
            for i, row in enumerate(rows):
                if i:
                    click.echo("")
                click.echo(format_trace_info(row))
        return

    try:
        results = replay_target(
            target,
            speed=speed,
            replay_time=replay_time,
            url=url,
            scenario=scenario,
            library=library,
            experiment=experiment,
            overlay=overlay,
            extra_ref=extra,
            only=only_names,
            extras=extra_files,
            align=align,
            wait=wait,
            recompute=recompute,
            trees=tuple(part.strip() for part in (trees or "").split(",") if part.strip()),
            record=record,
        )
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    dropped = False
    for row in results:
        if row.get("dropped"):
            dropped = True
            click.echo(
                f"{row.get('scenario')}: dropped n=0 path={row.get('path')}",
                err=True,
            )
            continue
        n = row.get("n")
        click.echo(
            f"{row.get('scenario')}: n={0 if n is None else n} "
            f"job={row.get('job_id') or 'ok'} path={row.get('path')}"
        )
    if dropped:
        raise click.ClickException(
            "ingest dropped — Reset had closed Passive ingest; retry this command"
        )


@demo_ui_group.command("replay")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--speed", type=float, default=None)
@click.option("--replay-time", type=float, default=None)
@click.option("--info", "show_info", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--url", default=None)
@click.option("--scenario", default=None)
@click.option("--library", default=None)
@click.option("--experiment", default=None)
@click.option("--overlay", default=None)
@click.option("--ref", "ref_json", default=None)
@click.option("--only", default=None)
@click.option("--align", type=click.Choice(["time", "gap"]), default="time")
@click.option("--extra", "extras", multiple=True)
@click.pass_context
def demo_ui_replay(
    ctx: click.Context,
    target: Path,
    speed: float | None,
    replay_time: float | None,
    show_info: bool,
    as_json: bool,
    url: str | None,
    scenario: str | None,
    library: str | None,
    experiment: str | None,
    overlay: str | None,
    ref_json: str | None,
    only: str | None,
    align: str,
    extras: tuple[str, ...],
) -> None:
    """Alias for ``mas-lab replay``."""
    ctx.invoke(
        replay_cmd,
        target=target,
        speed=speed,
        replay_time=replay_time,
        show_info=show_info,
        as_json=as_json,
        url=url,
        scenario=scenario,
        library=library,
        experiment=experiment,
        overlay=overlay,
        ref_json=ref_json,
        only=only,
        align=align,
        extras=extras,
        wait=True,
        recompute=False,
        trees=None,
        record=None,
    )


class ControllerCliComponent:
    """Entry-point hook for mas.lab.cli.components."""

    def register(self, app: click.Group) -> None:
        app.add_command(control_group, name="control")
        app.add_command(worker_group, name="worker")
        app.add_command(demo_ui_group, name="demo-ui")
        app.add_command(replay_cmd, name="replay")
