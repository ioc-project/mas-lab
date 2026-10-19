#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Replay native events.jsonl onto the live demo-ui ingest plane.

Default input is ``events.jsonl`` (a file, or a lab dir of cached traces).
A ui-trace directory is the other input: recorded envelopes, not a MAS
re-run. Chat / topology / series are live-folded unless those artefacts
are already in the ui-trace. Experiment pipelines are not run.

``mas-lab replay TRACE`` posts one run. ``mas-lab replay LAB`` posts cached
traces (``--only`` to pick lanes, ``--align time`` for a shared clock,
``--extra SCENARIO=PATH`` for JSON sidecars).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_REPLAY_TIME = 15.0

from mas.lab.controller.ui_stream.ui_trace import (
    cached_pipeline_files,
    discover_ui_traces,
    envelopes_digest,
    load_jsonl,
    read_ui_trace,
    write_ui_trace,
)


def ingest_url(url: str | None = None) -> str:
    raw = (
        url
        or os.environ.get("MAS_DEMO_UI_INGEST_URL")
        or os.environ.get("MAS_CONTROLLER_URL")
        or "http://127.0.0.1:8090"
    )
    base = raw.rstrip("/")
    if base.endswith("/api/ingest"):
        return base
    return base + "/api/ingest"


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            records.append(row)
    return records


def _scenario_of(path: Path) -> str:
    stem = path.stem
    if stem.endswith(".events"):
        stem = stem[: -len(".events")]
    if stem == "events":
        # output/<scenario>/<test>/<run>/traces/events.jsonl
        parts = path.parts
        if "output" in parts:
            idx = parts.index("output")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        if "trace" in parts:
            idx = parts.index("trace")
            if idx > 0:
                return parts[idx - 1]
        if "infra" in parts and ".cache" in parts:
            return path.parent.name
        return path.parent.name
    return stem


def discover_traces(target: Path) -> list[tuple[str, Path]]:
    """Return (scenario, events.jsonl) pairs for a file or a lab directory."""
    target = target.expanduser().resolve()
    if target.is_file():
        return [(_scenario_of(target), target)]
    if not target.is_dir():
        raise FileNotFoundError(target)
    hits: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        hits.append((_scenario_of(resolved), resolved))

    cache = target / "infra" / ".cache"
    if cache.is_dir():
        for path in sorted(cache.glob("*.events.jsonl")):
            add(path)
        for path in sorted(cache.glob("*.jsonl")):
            if path.name.endswith(".events.jsonl"):
                continue
            add(path)
    if not hits:
        for path in sorted(target.rglob("events.jsonl")):
            add(path)
    if not hits:
        for path in sorted(target.glob("*.events.jsonl")):
            add(path)
    return hits


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    from mas.lab.controller.ui_stream.auth import ingest_headers

    req = urllib.request.Request(
        url,
        data=body,
        headers=ingest_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ingest failed at {url}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": True, "raw": raw}
    return parsed if isinstance(parsed, dict) else {"ok": True, "raw": parsed}


def post_ingest(
    url: str,
    records: list[dict[str, Any]],
    *,
    ref: dict[str, Any],
    speed: float,
    timeout: float = 120.0,
) -> dict[str, Any]:
    return post_json(
        url,
        {
            "ref": ref,
            "records": records,
            "speed": speed,
            "replay": True,
            "nonce": str(uuid.uuid4()),
        },
        timeout=timeout,
    )


def aligned_url(url: str | None = None) -> str:
    dest = ingest_url(url)
    if dest.endswith("/api/ingest"):
        return dest + "/aligned"
    return dest.rstrip("/") + "/api/ingest/aligned"


def api_base(url: str | None = None) -> str:
    dest = ingest_url(url)
    if dest.endswith("/api/ingest"):
        return dest[: -len("/api/ingest")]
    return dest.rstrip("/")


def envelopes_url(url: str | None = None) -> str:
    return api_base(url) + "/api/ingest/envelopes"


def _bar(frac: float, width: int = 20) -> str:
    frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
    filled = int(round(frac * width))
    return "#" * filled + "." * (width - filled)


def format_replay_progress_line(job: dict[str, Any], *, wall_s: float | None = None) -> str:
    t0 = float(job.get("t0") or 0.0)
    t1 = float(job.get("t1") or 0.0)
    t = float(job.get("t") or t0)
    span = max(t1 - t0, 0.0)
    elapsed_t = max(0.0, t - t0) if span else 0.0
    frac_t = (elapsed_t / span) if span > 0 else 0.0
    n = int(job.get("n") or 0)
    n_total = int(job.get("n_total") or 0)
    frac_n = (n / n_total) if n_total else 0.0
    bits = [
        f"time [{_bar(frac_t)}] {elapsed_t:.1f}/{span:.1f}s",
        f"events [{_bar(frac_n, 16)}] {n}/{n_total}",
    ]
    trees = job.get("trees") or {}
    if isinstance(trees, dict):
        for name, prog in trees.items():
            if not isinstance(prog, dict):
                continue
            tn = int(prog.get("n") or 0)
            tt = int(prog.get("n_total") or 0)
            frac = (tn / tt) if tt else 0.0
            bits.append(f"{name} [{_bar(frac, 10)}] {tn}/{tt}")
    if wall_s is not None:
        bits.append(f"wall {wall_s:.1f}s")
    return "  ".join(bits)


def print_replay_sources(lanes: list[dict[str, Any]] | None) -> None:
    if not lanes:
        return
    print("demo-ui: sources", file=sys.stderr)
    for lane in lanes:
        name = str(lane.get("name") or lane.get("tree") or "?")
        source = str(lane.get("source") or "live")
        loc = str(lane.get("path") or lane.get("fold") or lane.get("tree") or "")
        print(f"  {name:<10} {source:<4} {loc}", file=sys.stderr)


def live_fold_lanes(path: Path | str | None = None) -> list[dict[str, Any]]:
    runtime = {"name": "runtime", "source": "file", "tree": "runtime"}
    if path:
        runtime["path"] = str(path)
    return [
        runtime,
        {"name": "chat", "source": "live", "fold": "LivePlotFolds"},
        {"name": "topology", "source": "live", "fold": "LivePlotFolds"},
        {"name": "series", "source": "live", "fold": "LivePlotFolds"},
    ]


def wait_job(
    url: str | None,
    job_id: str,
    *,
    timeout: float = 3600.0,
    expected_s: float | None = None,
    lanes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    started = time.time()
    base = api_base(url)
    hrefs = (f"{base}/api/ui/jobs/{job_id}", f"{base}/api/jobs/{job_id}")
    last: dict[str, Any] = {"id": job_id, "status": "running"}
    printed_sources = False
    if lanes:
        print_replay_sources(lanes)
        printed_sources = True
    while time.time() < deadline:
        for href in hrefs:
            try:
                with urllib.request.urlopen(href, timeout=10) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
                if isinstance(parsed, dict):
                    last = parsed
                    if not printed_sources and last.get("lanes"):
                        print_replay_sources(list(last.get("lanes") or []))
                        printed_sources = True
                    if parsed.get("status") in {"completed", "failed", "cancelled"}:
                        elapsed = time.time() - started
                        print(
                            f"\r{format_replay_progress_line(last, wall_s=elapsed)}",
                            file=sys.stderr,
                        )
                        print(f"demo-ui: {parsed.get('status')} in {elapsed:.1f}s", file=sys.stderr)
                        return parsed
                    break
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    pass
            except urllib.error.URLError:
                pass
        now = time.time()
        elapsed = now - started
        display = dict(last)
        if not display.get("t1") and expected_s and expected_s > 0:
            display["t0"] = 0.0
            display["t1"] = expected_s
            display["t"] = min(elapsed, expected_s)
        print(f"\r{format_replay_progress_line(display, wall_s=elapsed)}", end="", file=sys.stderr, flush=True)
        time.sleep(0.25)
    print("", file=sys.stderr)
    last["status"] = "timeout"
    return last


def save_trace(url: str | None, dest: Path, *, scenario: str | None = None) -> Path:
    qs = f"?scenario={scenario}" if scenario else ""
    req = urllib.request.Request(f"{api_base(url)}/api/trace{qs}", method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    runtime = body.get("runtime") or []
    pipeline = body.get("pipeline") or []
    return write_ui_trace(dest, runtime + pipeline, extra={"ref": body.get("ref") or {}})


def _record_ts(record: dict[str, Any]) -> float | None:
    raw = record.get("timestamp", record.get("ts", record.get("t")))
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value / 1000.0 if value > 1e12 else value
    if isinstance(raw, str) and raw.strip():
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def record_span(records: list[dict[str, Any]]) -> float:
    times = [ts for ts in (_record_ts(row) for row in records) if ts is not None]
    if not times:
        return 0.0
    return max(0.0, max(times) - min(times))


def replay_speed(
    records: list[dict[str, Any]],
    *,
    speed: float | None = None,
    replay_time: float | None = None,
) -> float:
    """Ingest clock. ``speed`` is an explicit factor (1 = recorded gaps, 0 = dump).

    ``replay_time`` is target wall seconds; the factor is duration / time.
    With neither set, replay_time is 15s.
    """
    if speed is not None:
        return 0.0 if float(speed) <= 0 else float(speed)
    wall = DEFAULT_REPLAY_TIME if replay_time is None else float(replay_time)
    if wall <= 0:
        return 0.0
    span = record_span(records)
    if span <= 0:
        return 1.0
    return span / wall


def trace_info(
    records: list[dict[str, Any]],
    *,
    path: Path | str | None = None,
    replay_time: float | None = None,
) -> dict[str, Any]:
    span = record_span(records)
    wall = DEFAULT_REPLAY_TIME if replay_time is None else float(replay_time)
    used = replay_speed(records, replay_time=wall)
    kinds = Counter(str(row.get("kind") or row.get("topic") or "") for row in records)
    agents = sorted(
        {
            str(row.get("agent_id") or (row.get("payload") or {}).get("agent_id") or "")
            for row in records
            if row.get("agent_id") or (isinstance(row.get("payload"), dict) and row["payload"].get("agent_id"))
        }
    )
    agents = [name for name in agents if name]
    return {
        "path": str(path) if path else "",
        "events": len(records),
        "duration_s": span,
        "agents": len(agents),
        "kinds": dict(kinds.most_common()),
        "replay_time_s": wall if wall > 0 else 0.0,
        "speed": used,
    }


def format_trace_info(info: dict[str, Any]) -> str:
    wall = float(info.get("replay_time_s") or 0.0)
    used = float(info.get("speed") or 0.0)
    lines: list[str] = []
    if info.get("path"):
        lines.append(str(info["path"]))
    lines.append(f"events    {info.get('events') or 0}")
    lines.append(f"duration  {float(info.get('duration_s') or 0):.2f}s")
    lines.append(f"agents    {info.get('agents') or 0}")
    if wall > 0 and used > 0:
        lines.append(f"speed     {used:.2f}×  (--speed {used:.2f}  or  --replay-time {wall:g})")
    elif used == 0:
        lines.append("speed     dump  (--speed 0)")
    kinds = info.get("kinds") or {}
    if isinstance(kinds, dict) and kinds:
        top = ", ".join(f"{name} {count}" for name, count in list(kinds.items())[:8])
        lines.append(f"kinds     {top}")
    return "\n".join(lines)


def describe_target(
    target: Path,
    *,
    only: tuple[str, ...] | list[str] | None = None,
    replay_time: float | None = None,
) -> list[dict[str, Any]]:
    wanted = {name.strip() for name in (only or ()) if str(name).strip()}
    ui_hits = discover_ui_traces(target)
    if ui_hits:
        if wanted:
            ui_hits = [(name, path) for name, path in ui_hits if name in wanted]
        out: list[dict[str, Any]] = []
        for name, path in ui_hits:
            buckets = read_ui_trace(path)
            rows = [row for tree in buckets.values() for row in (tree or [])]
            info = trace_info(rows, path=path, replay_time=replay_time)
            info["scenario"] = name
            out.append(info)
        if out:
            return out
    traces = discover_traces(target)
    if wanted:
        traces = [(name, path) for name, path in traces if name in wanted]
    if not traces:
        raise FileNotFoundError(f"no events.jsonl under {target}")
    out = []
    for name, path in traces:
        info = trace_info(load_records(path), path=path, replay_time=replay_time)
        info["scenario"] = name
        out.append(info)
    return out


def align_sidecar(records: list[dict[str, Any]], peer: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map round-index sidecar timestamps onto a peer trace's wall clock."""
    peer_ts = [ts for ts in (_record_ts(row) for row in peer) if ts is not None]
    if not records or not peer_ts:
        return records
    t0, t1 = min(peer_ts), max(peer_ts)
    span = max(t1 - t0, 1e-6)
    own = [ts for ts in (_record_ts(row) for row in records) if ts is not None]
    if own and max(own) >= 1e6:
        return records
    n = max(len(records) - 1, 1)
    out: list[dict[str, Any]] = []
    for i, row in enumerate(records):
        rec = dict(row)
        frac = 0.7 if len(records) == 1 else i / n
        rec["timestamp"] = t0 + span * frac
        out.append(rec)
    return out


def records_from_sidecar(path: Path, *, kind: str = "") -> list[dict[str, Any]]:
    """Turn a JSON or JSONL sidecar into timed records."""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        rows = json.loads(text)
        return [row for row in rows if isinstance(row, dict)]
    if stripped.startswith("{") and "\n{" not in text:
        doc = json.loads(text)
        if not isinstance(doc, dict):
            return []
        topic = kind or str(doc.get("kind") or "custom.snapshot")
        log = doc.get("log")
        if isinstance(log, list) and log:
            out: list[dict[str, Any]] = []
            for i, row in enumerate(log):
                snap = dict(doc)
                snap["kind"] = topic
                snap["log"] = log[: i + 1]
                if isinstance(row, dict):
                    snap["t"] = row.get("t", i)
                    snap["timestamp"] = row.get("t", i)
                    if row.get("x_star"):
                        snap["x_star"] = row.get("x_star")
                else:
                    snap["timestamp"] = i
                out.append(snap)
            return out
        doc = dict(doc)
        doc.setdefault("kind", topic)
        doc.setdefault("timestamp", doc.get("t") or 0)
        return [doc]
    return load_records(path)


def replay_ui_traces(
    hits: list[tuple[str, Path]],
    *,
    speed: float | None = None,
    replay_time: float | None = None,
    url: str | None = None,
    trees: tuple[str, ...] | None = None,
    recompute: bool = False,
    wait: bool = True,
) -> list[dict[str, Any]]:
    lanes = []
    results: list[dict[str, Any]] = []
    wanted = set(trees) if trees else {"runtime", "pipeline"}
    cached_artefacts: list[str] = []
    for name, path in hits:
        if recompute:
            runtime = read_ui_trace(path, trees=("runtime",)).get("runtime") or []
            if runtime:
                lanes.append({"tree": "runtime", "envelopes": runtime, "scenario": name, "path": str(path)})
            digest = envelopes_digest(runtime)
            for spec in cached_pipeline_files(path, digest):
                rows = load_jsonl(path / spec["path"]) if (path / spec["path"]).is_file() else []
                if not rows:
                    continue
                owner = str(spec.get("artefact") or "")
                cached_artefacts.append(owner)
                lanes.append({"tree": "pipeline", "envelopes": rows, "scenario": name, "path": str(path)})
            continue
        buckets = read_ui_trace(path, trees=tuple(wanted))
        for tree, rows in buckets.items():
            if rows:
                lanes.append({"tree": tree, "envelopes": rows, "scenario": name, "path": str(path)})
    if not lanes:
        raise FileNotFoundError("ui-trace has no envelopes for the requested trees")
    all_rows = [row for lane in lanes for row in (lane.get("envelopes") or [])]
    used = replay_speed(all_rows, speed=speed, replay_time=replay_time)
    span = record_span(all_rows)
    expected = (span / used) if used > 0 else 0.0
    print(
        f"demo-ui: pacing {len(all_rows)} events, trace={span:.1f}s, "
        f"speedup={used:.2f}×, wall~{expected:.0f}s",
        file=sys.stderr,
    )
    source_lanes: list[dict[str, Any]] = []
    for lane in lanes:
        source_lanes.append(
            {
                "name": lane["tree"],
                "source": "file",
                "tree": lane["tree"],
                "path": lane.get("path") or "",
            }
        )
    has_runtime = any(str(lane.get("tree") or "") == "runtime" for lane in lanes)
    if has_runtime:
        source_lanes.append({"name": "chat", "source": "live", "fold": "LivePlotFolds"})
    if recompute:
        held = {str(name) for name in cached_artefacts if "custom.chat" not in str(name)}
        cached_artefacts = [name for name in cached_artefacts if "custom.chat" not in str(name)]
        for name, artefact, fold in (
            ("topology", "custom.topology", "LivePlotFolds"),
            ("series", "series", "LivePlotFolds"),
        ):
            cached = any(artefact in token for token in held)
            if cached:
                source_lanes.append({"name": name, "source": "file", "path": artefact})
            else:
                source_lanes.append({"name": name, "source": "live", "fold": fold})
    posted = post_json(
        envelopes_url(url),
        {
            "speed": used,
            "recompute": recompute,
            "trees": sorted({lane["tree"] for lane in lanes}),
            "cached_artefacts": cached_artefacts,
            "replay": True,
            "lanes": [{"tree": lane["tree"], "envelopes": lane["envelopes"]} for lane in lanes],
        },
    )
    job_id = str(posted.get("job_id") or "")
    if wait and job_id and used > 0:
        posted.update(wait_job(url, job_id, expected_s=expected or None, lanes=source_lanes))
    elif source_lanes:
        print_replay_sources(source_lanes)
    posted["speed"] = used
    for lane in lanes:
        row = dict(posted)
        row["scenario"] = lane["scenario"]
        row["path"] = lane["path"]
        row["tree"] = lane["tree"]
        row["n"] = len(lane["envelopes"])
        results.append(row)
    return results


def replay_target(
    target: Path,
    *,
    speed: float | None = None,
    replay_time: float | None = None,
    url: str | None = None,
    scenario: str | None = None,
    library: str | None = None,
    experiment: str | None = None,
    overlay: str | None = None,
    extra_ref: dict[str, Any] | None = None,
    only: tuple[str, ...] | list[str] | None = None,
    extras: list[tuple[str, Path]] | None = None,
    align: str = "time",
    wait: bool = True,
    recompute: bool = False,
    trees: tuple[str, ...] | list[str] | None = None,
    record: Path | None = None,
) -> list[dict[str, Any]]:
    ui_hits = discover_ui_traces(target)
    wanted_names = {name.strip() for name in (only or ()) if str(name).strip()}
    if ui_hits and not extras:
        if wanted_names:
            ui_hits = [(name, path) for name, path in ui_hits if name in wanted_names]
        if ui_hits:
            results = replay_ui_traces(
                ui_hits,
                speed=speed,
                replay_time=replay_time,
                url=url,
                trees=tuple(trees) if trees else None,
                recompute=recompute,
                wait=wait,
            )
            if record:
                save_trace(url, Path(record), scenario=scenario)
            return results
    traces = discover_traces(target)
    if not traces:
        raise FileNotFoundError(f"no events.jsonl under {target}")
    wanted = {name.strip() for name in (only or ()) if str(name).strip()}
    if wanted:
        traces = [(name, path) for name, path in traces if name in wanted]
        if not traces:
            raise FileNotFoundError(f"no traces matching {sorted(wanted)}")
    lanes: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for name, path in traces:
        records = load_records(path)
        ref: dict[str, Any] = dict(extra_ref or {})
        ref["scenario"] = scenario if (scenario and len(traces) == 1 and not wanted) else name
        if library:
            ref["library"] = library
        if experiment:
            ref["experiment"] = experiment
        if overlay:
            ref["overlay"] = overlay
        lanes.append({"ref": ref, "records": records, "path": str(path), "scenario": ref["scenario"]})
    by_name = {lane["scenario"]: lane["records"] for lane in lanes}
    for scenario_name, extra_path in extras or []:
        records = records_from_sidecar(Path(extra_path))
        peer = by_name.get(scenario_name) or (lanes[0]["records"] if lanes else [])
        records = align_sidecar(records, peer)
        ref = dict(extra_ref or {})
        ref["scenario"] = scenario_name
        if library:
            ref["library"] = library
        if experiment:
            ref["experiment"] = experiment
        if overlay:
            ref["overlay"] = overlay
        lanes.append({"ref": ref, "records": records, "path": str(extra_path), "scenario": scenario_name})
    all_rows = [row for lane in lanes for row in (lane.get("records") or [])]
    used = replay_speed(all_rows, speed=speed, replay_time=replay_time)
    span = record_span(all_rows)
    expected = (span / used) if used > 0 else 0.0
    print(
        f"demo-ui: pacing {len(all_rows)} events, trace={span:.1f}s, "
        f"speedup={used:.2f}×, wall~{expected:.0f}s",
        file=sys.stderr,
    )
    use_align = align == "time" and len(lanes) > 1
    if use_align:
        posted = post_json(
            aligned_url(url),
            {
                "speed": used,
                "replay": True,
                "lanes": [{"ref": lane["ref"], "records": lane["records"]} for lane in lanes],
            },
        )
        job_id = str(posted.get("job_id") or "")
        source_lanes = live_fold_lanes(lanes[0]["path"] if lanes else None)
        for extra in lanes[1:]:
            source_lanes.append(
                {"name": extra["scenario"], "source": "file", "path": extra["path"], "tree": "runtime"}
            )
        if wait and job_id and used > 0:
            posted.update(wait_job(url, job_id, expected_s=expected or None, lanes=source_lanes))
        else:
            print_replay_sources(source_lanes)
        posted["speed"] = used
        for lane in lanes:
            row = dict(posted)
            row["scenario"] = lane["scenario"]
            row["path"] = lane["path"]
            row["n"] = len(lane["records"])
            row["align"] = "time"
            results.append(row)
        if record:
            save_trace(url, Path(record), scenario=scenario)
        return results
    dest = ingest_url(url)
    for lane in lanes:
        posted = post_ingest(dest, lane["records"], ref=lane["ref"], speed=used)
        job_id = str(posted.get("job_id") or "")
        source_lanes = live_fold_lanes(lane["path"])
        if wait and job_id and used > 0:
            posted.update(wait_job(url, job_id, expected_s=expected or None, lanes=source_lanes))
        else:
            print_replay_sources(source_lanes)
        posted["scenario"] = lane["scenario"]
        posted["path"] = lane["path"]
        posted["speed"] = used
        if posted.get("n") is None:
            posted["n"] = len(lane["records"])
        posted["align"] = "gap"
        results.append(posted)
    if record:
        save_trace(url, Path(record), scenario=scenario)
    return results
