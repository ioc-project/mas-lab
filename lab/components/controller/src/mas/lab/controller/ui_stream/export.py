#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Build a static demo-ui site that replays envelopes with no backend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mas.lab.controller.ui_stream.bus import EventBus
from mas.lab.controller.ui_stream.ingest import NativeIngest
from mas.lab.controller.ui_stream.layouts import load_layout
from mas.lab.controller.ui_stream.metrics import MetricsStore
from mas.lab.controller.ui_stream.refs import DataSourceRef, envelope
from mas.lab.controller.ui_stream.replay import (
    custom_panel_records,
    demo_records,
    merge_replay_records,
    parse_jsonl,
    trace_metric_records,
)
from mas.lab.controller.ui_stream.spec_snapshot import snapshot_from_records

BUNDLE_VERSION = 1


def find_demo_ui_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        root = Path(explicit)
        if (root / "index.html").is_file():
            return root
        nested = root / "demo-ui"
        if (nested / "index.html").is_file():
            return nested
        raise FileNotFoundError(f"demo-ui not found under {root}")
    env = os.environ.get("MAS_DEMO_UI_ROOT")
    if env:
        return find_demo_ui_root(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "demo-ui"
        if (cand / "index.html").is_file():
            return cand
    raise FileNotFoundError("demo-ui/index.html not found; set MAS_DEMO_UI_ROOT")


def default_layouts_root(ui_root: Path | None = None) -> Path:
    override = os.environ.get("MAS_DEMO_UI_LAYOUTS")
    if override:
        return Path(override)
    root = ui_root or find_demo_ui_root()
    return root / "layouts"


def _sibling(anchor: Path, name: str) -> Path | None:
    """Look for ``name`` next to ``anchor`` first, then one directory up (the
    same two-level search every other sibling artefact here already uses,
    since ``events.jsonl`` may sit directly in the run dir or under a nested
    ``traces/`` subdirectory)."""
    candidate = anchor.parent / name
    if candidate.is_file():
        return candidate
    candidate = anchor.parent.parent / name
    return candidate if candidate.is_file() else None


def resolve_trace_paths(
    trace: Path,
) -> tuple[Path, Path | None, Path | None, dict[str, Path]]:
    """Return (events.jsonl, metrics.json, progress.jsonl, {owner: custom.{owner}.jsonl})
    from a file or directory. The fourth element covers
    ``custom.{owner}.jsonl`` sibling traces (same convention as
    metrics.json/progress.jsonl -- see file_tail.append_custom_trace) so an
    offline export carries the same panel data a live run or online replay
    would have."""
    path = Path(trace)
    events: Path | None = None
    if path.is_file() and path.name.endswith(".jsonl"):
        events = path
    elif path.is_dir():
        for candidate in (path / "events.jsonl", path / "traces" / "events.jsonl"):
            if candidate.is_file():
                events = candidate
                break
    if events is None:
        raise FileNotFoundError(f"no events.jsonl under {trace}")
    metrics = _sibling(events, "metrics.json")
    progress = _sibling(events, "progress.jsonl")
    # custom.{owner}.jsonl siblings (file_tail.append_custom_trace) --
    # discovered by glob, not a hardcoded owner list.
    custom: dict[str, Path] = {}
    for folder in (events.parent, events.parent.parent):
        if not folder.is_dir():
            continue
        for found in folder.glob("custom.*.jsonl"):
            owner = found.stem.split(".", 1)[1] if found.stem.count(".") >= 1 else ""
            if owner:
                custom.setdefault(owner, found)
    return events, metrics, progress, custom


def build_envelopes(
    records: list[dict[str, Any]],
    ref: DataSourceRef,
    *,
    spec: dict[str, Any] | None = None,
    extra_metrics: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    bus = EventBus(max_history=None)
    ingest = NativeIngest(bus, MetricsStore())
    topology = spec or snapshot_from_records(records)
    bus.publish(envelope("spec.snapshot", ref, "spec", topology, replay=False))
    ordered = merge_replay_records(demo_records(records), extra_metrics or [])
    for record in ordered:
        ingest.ingest(ref, record, replay=False)
    ingest.flush_snapshot(ref)
    return [item.as_dict() for item in bus.snapshot(("*",), DataSourceRef())]


def build_bundle(
    trace: Path,
    *,
    layout_name: str,
    layouts_root: Path | None = None,
    ref: DataSourceRef | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events_path, metrics_path, progress_path, custom_paths = resolve_trace_paths(Path(trace))
    records = parse_jsonl(events_path.read_text(encoding="utf-8"))
    extra = trace_metric_records(metrics=metrics_path, progress=progress_path, events=records)
    for owner_path in custom_paths.values():
        extra.extend(custom_panel_records(owner_path))
    scoped = ref or DataSourceRef(mode="replay")
    ui_root = None
    try:
        ui_root = find_demo_ui_root()
    except FileNotFoundError:
        ui_root = None
    root = layouts_root or (default_layouts_root(ui_root) if ui_root else None)
    layout: dict[str, Any] = {}
    if root is not None:
        layout = load_layout(root, layout_name)
    if layout.get("dataSource"):
        scoped = scoped.merge(layout.get("dataSource"))
    envelopes = build_envelopes(records, scoped, spec=spec, extra_metrics=extra)
    return {
        "version": BUNDLE_VERSION,
        "layoutName": layout_name,
        "layout": layout,
        "ref": scoped.as_dict(),
        "envelopes": envelopes,
    }


def build_merged_bundle(
    scenario_traces: dict[str, Path],
    *,
    layout_name: str,
    layouts_root: Path | None = None,
    ref: DataSourceRef | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one bundle covering several scenarios at once -- for a
    multi-panel compare layout where each panel scopes itself to one
    ``dataSource.scenario``.

    ``build_bundle`` builds one scenario's envelopes under one
    ``DataSourceRef`` (one ``scenario``). The live and online-replay paths
    already support several panels sharing one WS/bundle stream: the
    frontend's own per-panel filter (``demo-ui/src/bus.js``'s
    ``ref.scenario !== envelope.ref.scenario`` check) decides which
    envelopes a given panel accepts, keyed off each envelope's own
    ``ref.scenario`` -- not off which bundle/stream it came from. So a
    merged *offline* bundle only has to put every scenario's own envelopes
    (each correctly tagged with its own ``ref.scenario``) into one combined
    ``envelopes`` list; no frontend change is needed, because the same
    per-panel filter that already runs for live/online-replay runs for an
    offline bundle too.

    ``scenario_traces`` maps each panel's ``scenario`` slug (exactly what
    that panel's ``dataSource.scenario`` says in the layout) to that
    scenario's own trace (an ``events.jsonl`` file or a trace directory,
    same as ``build_bundle``'s single-scenario ``trace`` argument).
    """
    scoped_base = ref or DataSourceRef(mode="replay")
    ui_root = None
    try:
        ui_root = find_demo_ui_root()
    except FileNotFoundError:
        ui_root = None
    root = layouts_root or (default_layouts_root(ui_root) if ui_root else None)
    layout: dict[str, Any] = {}
    if root is not None:
        layout = load_layout(root, layout_name)
    if layout.get("dataSource"):
        scoped_base = scoped_base.merge(layout.get("dataSource"))

    merged_envelopes: list[dict[str, Any]] = []
    for scenario, trace in scenario_traces.items():
        events_path, metrics_path, progress_path, custom_paths = resolve_trace_paths(Path(trace))
        records = parse_jsonl(events_path.read_text(encoding="utf-8"))
        extra = trace_metric_records(metrics=metrics_path, progress=progress_path, events=records)
        for owner_path in custom_paths.values():
            extra.extend(custom_panel_records(owner_path))
        scoped = scoped_base.merge(DataSourceRef(scenario=scenario))
        merged_envelopes.extend(build_envelopes(records, scoped, spec=spec, extra_metrics=extra))

    return {
        "version": BUNDLE_VERSION,
        "layoutName": layout_name,
        "layout": layout,
        "ref": scoped_base.as_dict(),
        "envelopes": merged_envelopes,
    }


def export_merged_trace(
    scenario_traces: dict[str, Path],
    out_dir: Path,
    *,
    layout_name: str,
    layouts_root: Path | None = None,
    ref: DataSourceRef | None = None,
    ui_root: Path | None = None,
    dist_dir: Path | None = None,
    build_ui: bool = True,
) -> Path:
    bundle = build_merged_bundle(
        scenario_traces,
        layout_name=layout_name,
        layouts_root=layouts_root,
        ref=ref,
    )
    return write_site(
        Path(out_dir),
        bundle,
        ui_root=ui_root,
        dist_dir=dist_dir,
        build_ui=build_ui,
    )


def _write_bundle_js(path: Path, bundle: dict[str, Any]) -> None:
    payload = json.dumps(bundle, ensure_ascii=False)
    path.write_text(f"window.__DEMO_UI_BUNDLE__ = {payload};\n", encoding="utf-8")


def _patch_index(html: str) -> str:
    inject = (
        '<script>window.__DEMO_UI_OFFLINE__=true;</script>\n'
        '<script src="./bundle.js"></script>\n'
    )
    if "window.__DEMO_UI_OFFLINE__" in html:
        return html
    if "<head>" in html:
        return html.replace("<head>", "<head>\n" + inject, 1)
    if "</head>" in html:
        return html.replace("</head>", inject + "</head>", 1)
    return inject + html


def build_static_ui(ui_root: Path, *, base: str = "./") -> Path:
    dist = ui_root / "dist"
    env = os.environ.copy()
    env["DEMO_UI_BASE"] = base
    subprocess.run(
        ["npm", "run", "build"],
        cwd=ui_root,
        env=env,
        check=True,
    )
    if not dist.is_dir():
        raise FileNotFoundError(f"vite dist missing at {dist}")
    return dist


def write_site(
    out_dir: Path,
    bundle: dict[str, Any],
    *,
    ui_root: Path | None = None,
    dist_dir: Path | None = None,
    build_ui: bool = True,
) -> Path:
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    root = ui_root
    if dist_dir is None:
        root = root or find_demo_ui_root()
        existing = root / "dist"
        if build_ui or not existing.is_dir():
            dist_dir = build_static_ui(root)
        else:
            dist_dir = existing
    if dist_dir is not None:
        for item in Path(dist_dir).iterdir():
            target = dest / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    (dest / "bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_bundle_js(dest / "bundle.js", bundle)
    index = dest / "index.html"
    if index.is_file():
        index.write_text(_patch_index(index.read_text(encoding="utf-8")), encoding="utf-8")
    else:
        index.write_text(
            _patch_index(
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>MAS Lab demo UI</title></head><body><div id='app'></div></body></html>"
            ),
            encoding="utf-8",
        )
    (dest / "README.txt").write_text(
        "Offline demo UI. Serve this directory (ES modules need http):\n"
        "  python3 -m http.server 8766 --bind 127.0.0.1\n"
        "  open http://127.0.0.1:8766/\n",
        encoding="utf-8",
    )
    return dest


def export_trace(
    trace: Path,
    out_dir: Path,
    *,
    layout_name: str = "run-view.yaml",
    layouts_root: Path | None = None,
    ref: DataSourceRef | None = None,
    ui_root: Path | None = None,
    dist_dir: Path | None = None,
    build_ui: bool = True,
) -> Path:
    bundle = build_bundle(
        Path(trace),
        layout_name=layout_name,
        layouts_root=layouts_root,
        ref=ref,
    )
    return write_site(
        Path(out_dir),
        bundle,
        ui_root=ui_root,
        dist_dir=dist_dir,
        build_ui=build_ui,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import webbrowser
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    parser = argparse.ArgumentParser(description="Export a static demo-ui replay from a native trace.")
    parser.add_argument("trace", type=Path, help="events.jsonl or a trace/run directory")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output directory")
    parser.add_argument("--layout", default="run-view.yaml")
    parser.add_argument("--layouts", type=Path, default=None)
    parser.add_argument("--no-build", action="store_true", help="Reuse demo-ui/dist if present")
    parser.add_argument("--serve", action="store_true", help="Serve the export and open a browser")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    dest = export_trace(
        args.trace,
        args.output,
        layout_name=args.layout,
        layouts_root=args.layouts,
        build_ui=not args.no_build,
    )
    print(f"Wrote {dest}")
    if not args.serve:
        print(f"python3 -m http.server {args.port} --bind 127.0.0.1 --directory {dest}")
        return 0

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=str(dest), **kw)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(url)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
