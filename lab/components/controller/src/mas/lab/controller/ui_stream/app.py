#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""UI stream plane hosted by mas-lab serve: envelopes, /ws, commands, replay-by-id."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from mas.lab.controller.ui_stream.auth import authorize_http, authorize_ws, security_headers
from mas.lab.controller.ui_stream.bus import EventBus
from mas.lab.controller.ui_stream.governance import GovernanceStore
from mas.lab.controller.ui_stream.ingest import NativeIngest
from mas.lab.controller.ui_stream.inspect import extra_native_kinds, infer_inspect
from mas.lab.controller.ui_stream.layouts import load_layout
from mas.lab.controller.ui_stream.metrics import MetricsStore
from mas.lab.controller.ui_stream.refs import (
    STREAMS,
    DataSourceRef,
    envelope,
    envelope_from_mapping,
    ref_from_mapping,
    reset_clock,
    reset_nonce,
    use_clock,
    use_nonce,
)
from mas.lab.controller.ui_stream.timeline import (
    envelopes_to_scheduled,
    merge_timeline,
    pump_timeline,
    records_to_scheduled,
)
from mas.lab.controller.ui_stream.ui_trace import split_envelopes, write_ui_trace
from mas.lab.controller.ui_stream.replay import (
    custom_panel_records,
    demo_records,
    merge_replay_records,
    parse_jsonl,
    trace_metric_records,
)
from mas.lab.controller.ui_stream.run_index import RunRecord, infer_mas_rel, resolve_run
from mas.lab.controller.ui_stream.spec_load import snapshot_for_ref
from mas.lab.controller.ui_stream.stream import command_payload, is_command_frame, parse_frame
from mas.runtime.constants import WORKSPACE_CONFIG_FILENAME
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

UI_ORIGIN = "demo-ui"
CONTROL_VERBS = frozenset(
    {
        "pause",
        "resume",
        "steer",
        "enqueue_input",
        "send_message",
        "navigate",
        "inspect",
        "inspect_context",
        "list_checkpoints",
        "cancel_inflight",
        "snapshot",
        "persist",
    }
)


def _stock_layouts() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "demo-ui" / "layouts"
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "demo-ui" / "layouts"


def layouts_root() -> Path:
    override = os.environ.get("MAS_DEMO_UI_LAYOUTS")
    if override:
        first = override.split(os.pathsep)[0].strip()
        if first:
            return Path(first).expanduser()
    return _stock_layouts()


def layout_roots() -> list[Path]:
    seen: set[Path] = set()
    roots: list[Path] = []

    def _add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if resolved.is_dir() and resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)

    _add(_stock_layouts())
    for raw in os.environ.get("MAS_DEMO_UI_LAYOUTS", "").split(os.pathsep):
        raw = raw.strip()
        if raw:
            _add(Path(raw))
    for raw in os.environ.get("MAS_LIBRARY_PATHS", "").split(os.pathsep):
        raw = raw.strip()
        if not raw:
            continue
        base = Path(raw).expanduser()
        _add(base / "demo-ui" / "layouts")
        if base.name == "layouts":
            _add(base)
        if base.is_dir():
            nested = (
                list(base.glob("*/demo-ui/layouts"))
                + list(base.glob("*.lab/demo-ui/layouts"))
                + list(base.glob("labs/*.lab/demo-ui/layouts"))
            )
            for layouts in nested:
                _add(layouts)
    return roots


def _controller_ingest_url() -> str:
    base = os.environ.get("MAS_CONTROLLER_URL") or os.environ.get("MAS_DEMO_UI_INGEST_URL") or "http://127.0.0.1:8090"
    return base.rstrip("/") + ("/api/ingest" if not base.rstrip("/").endswith("/api/ingest") else "")


class UiStreamState:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.metrics = MetricsStore()
        self.governance = GovernanceStore()
        self.ingest = NativeIngest(self.bus, self.metrics, self.governance)
        self.ingest._demand = self.wants_inspect
        self.ingest._on_inspect = self._schedule_inspect
        self._inspect_ids: dict[int, tuple[str, ...]] = {}
        self._inspect_tasks: dict[str, asyncio.Task[None]] = {}
        self._job_seen: dict[str, str] = {}
        self._job_refs: dict[str, DataSourceRef] = {}
        self._runs: list[dict[str, Any]] = []
        self._deleted: set[str] = set()
        self._indexed: dict[str, RunRecord] = {}
        self._replay_tasks: dict[str, asyncio.Task[None]] = {}
        self._job_progress: dict[str, dict[str, Any]] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._closed_until_run = False
        self._replay_nonce: str | None = None

    def lookup_job(self, job_id: str) -> dict[str, Any] | None:
        """Replay/UI job status. Used by GET /api/jobs/{id} (controller route wins)."""
        if job_id in self._deleted:
            return None
        status = self._job_seen.get(job_id)
        if not status and job_id not in self._replay_tasks:
            return None
        ref = self._job_refs.get(job_id)
        payload: dict[str, Any] = {
            "id": job_id,
            "status": status or "running",
            "origin": UI_ORIGIN,
            "ref": ref.as_dict() if ref else {},
        }
        progress = self._job_progress.get(job_id)
        if isinstance(progress, dict):
            payload.update(progress)
        return payload

    def reset(self) -> None:
        self.bus.clear()
        self.metrics.clear()
        self.governance.clear()
        self.ingest.clear()
        self._job_seen.clear()
        self._job_refs.clear()
        self._job_progress.clear()
        self._runs.clear()
        self._deleted.clear()
        self._indexed.clear()
        self._closed_until_run = False
        self._inspect_ids.clear()
        for task in list(self._inspect_tasks.values()):
            task.cancel()
        self._inspect_tasks.clear()
        for task in list(self._replay_tasks.values()):
            task.cancel()
        self._replay_tasks.clear()
        self._replay_nonce = None

    def wants_inspect(self, pull_id: str, ref: DataSourceRef) -> bool:
        from mas.lab.controller.ui_stream.inspect import PULLS

        spec = PULLS.get(pull_id)
        for sub in self.bus._subs:
            if pull_id in self._inspect_ids.get(id(sub), ()) and sub.ref.matches(ref):
                return True
        if spec is not None and self.bus.demands_topic(spec.topic, ref):
            return True
        return False

    def inspect_native_kinds(self, ref: DataSourceRef) -> frozenset[str]:
        ids: list[str] = []
        for sub in self.bus._subs:
            ids.extend(self._inspect_ids.get(id(sub), infer_inspect(sub.topics)))
        if self.wants_inspect("context", ref) and "context" not in ids:
            ids.append("context")
        return extra_native_kinds(ids)

    def _schedule_inspect(self, pull_id: str, ref: DataSourceRef) -> None:
        if pull_id != "context" or not ref.session:
            return
        if not self.wants_inspect("context", ref):
            return
        key = ref.session
        existing = self._inspect_tasks.get(key)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._inspect_tasks[key] = loop.create_task(_pull_inspect_context(ref), name=f"inspect-ctx-{key[:8]}")

    async def close(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
        self.reset()


state = UiStreamState()
router = APIRouter()
_MAX_INGEST_RECORDS = 20_000
_MAX_REPLAY_TASKS = 8


def _prune_replay_tasks() -> None:
    for job_id, task in list(state._replay_tasks.items()):
        if task.done():
            state._replay_tasks.pop(job_id, None)


def _guard_ingest(n: int) -> None:
    if n > _MAX_INGEST_RECORDS:
        raise HTTPException(status_code=413, detail="ingest payload too large")
    _prune_replay_tasks()
    if len(state._replay_tasks) >= _MAX_REPLAY_TASKS:
        raise HTTPException(status_code=429, detail="too many ingest jobs")


def _register_replay_task(job_id: str, task: asyncio.Task[None]) -> None:
    state._replay_tasks[job_id] = task


def _lib_dir(library: str) -> Path:
    from mas.lab.controller.deps import get_library_path

    return get_library_path(library)


def _abs_infra(lib_dir: Path, ref: str) -> str:
    path = Path(ref)
    if not path.is_absolute():
        path = (lib_dir / path).resolve()
    return str(path)


def _workspace_infra_refs(lib_dir: Path) -> list[str]:
    """Library workspace-config infra, else an SRE demo bundle if present."""
    refs: list[str] = []
    cfg = lib_dir / WORKSPACE_CONFIG_FILENAME
    if cfg.is_file():
        try:
            from mas.runtime.spec.source import load_yaml_file

            data = load_yaml_file(cfg) or {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        raw = data.get("infra_refs") or data.get("default_infra")
        if isinstance(raw, str) and raw.strip():
            refs = [raw.strip()]
        elif isinstance(raw, list):
            refs = [str(item).strip() for item in raw if str(item).strip()]
    if not refs:
        bundle = lib_dir / "infra" / "demo.yaml"
        if bundle.is_file():
            refs = [str(bundle)]
    return [_abs_infra(lib_dir, item) for item in refs]


def _scenario_cache_infra(lib_dir: Path, scenario: str) -> list[str]:
    name = str(scenario or "").strip().lower()
    if not name:
        return []
    infra = lib_dir / "infra"
    for filename in (
        f"llm-cache-demo-{name}-replay.yaml",
        f"llm-cache-{name}-replay.yaml",
    ):
        path = infra / filename
        if path.is_file():
            return [str(path.resolve())]
    return []


def _live_infra(lib_dir: Path, targets: list[DataSourceRef]) -> tuple[list[str], dict[str, list[str]]]:
    base = _workspace_infra_refs(lib_dir)
    per: dict[str, list[str]] = {}
    scenarios = [str(item.scenario) for item in targets if item.scenario]
    if not scenarios:
        infra = lib_dir / "infra"
        if infra.is_dir():
            for path in sorted(infra.glob("llm-cache-demo-*-replay.yaml")):
                stem = path.stem.replace("llm-cache-demo-", "").replace("-replay", "")
                if stem:
                    scenarios.append(stem)
    for scenario in scenarios:
        extra = _scenario_cache_infra(lib_dir, scenario)
        if extra:
            per[scenario] = [*base, *extra]
    return base, per


def _live_overlays(targets: list[DataSourceRef]) -> dict[str, list[str]]:
    """Lane overlays from the layout (e.g. baseline-round-cap) keyed by scenario."""
    per: dict[str, list[str]] = {}
    for item in targets:
        scenario = str(item.scenario or "").strip()
        overlay = str(item.overlay or "").strip()
        if not scenario or not overlay:
            continue
        bucket = per.setdefault(scenario, [])
        if overlay not in bucket:
            bucket.append(overlay)
    return per


def _session_dir() -> Path:
    raw = os.environ.get("MAS_SESSION_DIRECTORY") or os.environ.get("MAS_CONTROL_DIRECTORY")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "mas" / "sessions"


async def _pull_inspect_context(ref: DataSourceRef) -> None:
    """Live inspect_context only while a UI plugin has subscribed to it."""
    if not ref.session or not state.wants_inspect("context", ref):
        return
    try:
        from mas.library.standard.plugins.control.attach import attach
        from mas.library.standard.plugins.control.directory import FileSessionDirectory
        from mas.library.standard.plugins.governance.context_usage import snapshot_from_view
    except Exception:
        return
    try:
        client = await attach(FileSessionDirectory(_session_dir()), str(ref.session))
    except Exception:
        return
    try:
        view = await client.call("inspect_context", str(ref.session))
    except Exception:
        logger.debug("inspect_context skipped", exc_info=True)
        return
    finally:
        try:
            await client.close()
        except Exception:
            pass
    payload = snapshot_from_view(view if isinstance(view, dict) else {})
    if not payload:
        return
    payload["kind"] = "context_usage"
    stats = state.governance.apply(ref, payload)
    stats.pop("_updated", None)
    state.bus.publish(envelope("governance.context", ref, "governance", stats))


def _stamp_ui(ref: DataSourceRef) -> DataSourceRef:
    return ref.merge({"origin": UI_ORIGIN})


def _streams() -> dict[str, str]:
    return dict(STREAMS)


class CommandBody(BaseModel):
    verb: str
    ref: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    mode: str = "inject_now"
    reason: str = "demo-ui"
    to: str = ""
    query: str = ""
    overlays: list[str] = Field(default_factory=list)
    infra_refs: list[str] = Field(default_factory=list)
    timeout: int = 0
    targets: list[dict[str, Any]] = Field(default_factory=list)
    pre: list[dict[str, Any]] = Field(default_factory=list)
    experiment: str = ""
    batch: str = ""
    speed: float = 0.0
    nonce: str = ""


class IngestBody(BaseModel):
    ref: dict[str, Any] = Field(default_factory=dict)
    record: dict[str, Any] | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    replay: bool = False
    speed: float = 0.0
    nonce: str = ""


class AlignedLane(BaseModel):
    ref: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)


class AlignedIngestBody(BaseModel):
    lanes: list[AlignedLane] = Field(default_factory=list)
    speed: float = 1.0


class EnvelopeLane(BaseModel):
    tree: str = "runtime"
    envelopes: list[dict[str, Any]] = Field(default_factory=list)


class EnvelopeIngestBody(BaseModel):
    lanes: list[EnvelopeLane] = Field(default_factory=list)
    speed: float = 1.0
    recompute: bool = False
    trees: list[str] = Field(default_factory=list)
    cached_artefacts: list[str] = Field(default_factory=list)


class StreamFrameBody(BaseModel):
    topic: str = ""
    ref: dict[str, Any] = Field(default_factory=dict)
    source: str = "artifact"
    payload: Any = None
    replay: bool = False
    id: str | None = None
    verb: str = ""


@router.get("/api/catalog/libraries")
async def catalog_libraries() -> dict[str, Any]:
    from mas.lab.controller.deps import get_manifest_store

    return {"libraries": get_manifest_store().libraries()}


@router.get("/api/catalog/libraries/{library}/experiments")
async def catalog_experiments(library: str) -> dict[str, Any]:
    from mas.lab.controller.deps import get_manifest_store

    return {"experiments": get_manifest_store().list_experiments(library)}


@router.get("/api/catalog/libraries/{library}/overlays")
async def catalog_overlays(library: str) -> Any:
    from mas.lab.controller.deps import get_manifest_store

    return {"overlays": get_manifest_store().list_overlays(library)}


@router.get("/api/sessions")
async def sessions() -> dict[str, Any]:
    try:
        from mas.library.standard.plugins.control.directory import FileSessionDirectory

        directory = FileSessionDirectory(_session_dir())
        rows = []
        for ad in directory.list():
            rows.append({**ad.as_dict(), "live": directory.is_live(ad.session_id), "token": ""})
        return {"sessions": rows}
    except Exception:
        return {"sessions": []}


@router.get("/api/runs")
async def list_runs() -> dict[str, Any]:
    return {"runs": _runs_payload()}


def _artefact_catalog() -> list[dict[str, Any]]:
    from mas.lab.lab.config.artifact_types import list_artifact_types

    rows: list[dict[str, Any]] = []
    for name, info in list_artifact_types().items():
        if not (info.get("realtime") or info.get("stream") or info.get("plot")):
            continue
        rows.append({"name": name, **info})
    return rows


def _artefact_info(name: str) -> dict[str, Any]:
    from mas.lab.controller.ui_stream.artefact_bind import LIVE_GRAPH, SHORTCUTS, canonical_artefact, link_for
    from mas.lab.lab.config.artifact_types import type_info

    key = str(name or "").strip()
    info = type_info(key)
    if not info:
        try:
            info = type_info(canonical_artefact(key))
        except Exception:
            info = {}
    if info:
        return {"name": key, **info}
    for row in _artefact_catalog():
        if row.get("name") == key:
            return row
    canon = canonical_artefact(key)
    if canon in LIVE_GRAPH or key in SHORTCUTS:
        link = link_for(canon)
        return {
            "name": key,
            "stream": link.stream,
            "plot": link.plot,
            "realtime": True,
            "address": link.address,
        }
    if key.startswith("series.") or ("." not in key and key):
        stream = key if key.startswith("series.") else f"series.{key}"
        return {
            "name": key[7:] if key.startswith("series.") else key,
            "stream": stream,
            "plot": "series",
            "realtime": True,
        }
    raise KeyError(key)


def _plot_info(kind: str) -> dict[str, str]:
    """Kind plus ``stream_for(kind)`` — the artefact owner the plot consumes."""
    name = str(kind or "").strip()
    try:
        from mas.lab.plots.providers import plot_info

        return plot_info(name)
    except (ImportError, KeyError):
        pass
    try:
        row = _artefact_info(name)
        stream = str(row.get("stream") or "")
        plot = str(row.get("plot") or "")
        if stream and plot and plot != "series":
            return {
                "kind": plot,
                "stream": stream,
                "artefact": str(row.get("name") or name),
            }
    except KeyError:
        pass
    raise KeyError(name)


def _latest_artefact_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl" or path.name.endswith(".jsonl"):
        last = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            last = json.loads(line)
        return last
    if path.suffix in {".json", ".yaml", ".yml"}:
        if path.suffix == ".json":
            return json.loads(text)
        import yaml

        return yaml.safe_load(text)
    return None


def _artefact_file(record: RunRecord, name: str) -> Path | None:
    artefacts = record.artefacts or {}
    token = name.replace("-", "_")
    for key in (name, token, f"custom_{name}", f"custom_{token}"):
        path = artefacts.get(key)
        if path:
            return Path(path)
    return None


def _render_plot_html(kind: str, fmt: str = "html") -> str | bytes:
    name = str(kind or "").strip()
    fmt = str(fmt or "html").lower()
    try:
        from mas.lab.plots.providers import render_plot

        return render_plot(name, {}, fmt=fmt)
    except (ImportError, KeyError):
        pass
    raise KeyError(name)


@router.get("/api/artefacts")
async def list_artefacts() -> dict[str, Any]:
    return {"artefacts": _artefact_catalog()}


def _artefact_body(name: str, ref: DataSourceRef, info: dict[str, Any], payload: Any) -> dict[str, Any]:
    from mas.lab.controller.ui_stream.artefact_bind import is_sealed_topic, link_for

    link = link_for(name, ref.as_dict())
    stream = str(info.get("stream") or link.stream or "")
    ticks = state.bus.snapshot([stream, f"{stream}.*"], ref) if stream else []
    complete = any(
        (getattr(item, "complete", None) is True) or is_sealed_topic(str(getattr(item, "topic", "") or ""))
        for item in ticks
    )
    return {
        **link.as_dict(),
        "name": name,
        "artefact": link.name,
        "stream": stream or link.stream,
        "plot": info.get("plot") or link.plot,
        "ref": ref.as_dict(),
        "payload": payload,
        "complete": complete,
    }


def _bus_latest_artefact(stream: str, ref: DataSourceRef) -> Any:
    if not stream:
        return None
    globs = [stream, f"{stream}.*"]
    events = state.bus.snapshot(globs, ref)
    if not events and ref.scenario:
        events = state.bus.snapshot(globs, DataSourceRef(scenario=ref.scenario))
    if not events:
        events = state.bus.snapshot(globs, DataSourceRef())
    return events[-1].payload if events else None


@router.get("/api/artefacts/{name}")
async def get_artefact_latest(
    name: str,
    library: str | None = None,
    experiment: str | None = None,
    scenario: str | None = None,
    test: str | None = None,
    run: str | None = None,
) -> dict[str, Any]:
    if not name or "/" in name or name.startswith("."):
        raise HTTPException(status_code=404, detail="unknown artefact")
    try:
        info = _artefact_info(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown artefact {name}") from exc
    ref = ref_from_mapping(
        {
            "library": library,
            "experiment": experiment,
            "scenario": scenario,
            "test": test,
            "run": run,
        }
    )
    payload = _bus_latest_artefact(str(info.get("stream") or ""), ref)
    return _artefact_body(str(info.get("name") or name), ref, info, payload)


@router.get("/api/artefacts/{library}/{experiment}/{scenario}/{test}/{run}/{name}")
async def get_artefact(
    library: str,
    experiment: str,
    scenario: str,
    test: str,
    run: str,
    name: str,
) -> dict[str, Any]:
    from mas.lab.lab.config.artifact_types import ref_from_artefact_path

    if not name or "/" in name or name.startswith("."):
        raise HTTPException(status_code=404, detail="unknown artefact")
    try:
        info = _artefact_info(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown artefact {name}") from exc
    ref_map = ref_from_artefact_path(library, experiment, scenario, test, run)
    ref = ref_from_mapping(ref_map)
    payload = None
    if ref.library:
        record = resolve_run(_lib_dir(ref.library), ref, indexed=state._indexed)
        path = _artefact_file(record, str(info.get("name") or name))
        payload = _latest_artefact_payload(path) if path and path.is_file() else None
    if payload is None:
        payload = _bus_latest_artefact(str(info.get("stream") or ""), ref)
    return _artefact_body(str(info.get("name") or name), ref, info, payload)


@router.get("/api/plots/assets/d3.min.js", response_model=None)
async def plot_d3_asset(_: None = Depends(authorize_http)):
    from mas.lab.plots.live_html import d3_inline

    return Response(
        content=d3_inline(),
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/plots/{kind}/draw.js", response_model=None)
async def plot_draw_js(kind: str, _: None = Depends(authorize_http)):
    if not kind or "/" in kind or kind.startswith("."):
        raise HTTPException(status_code=404, detail="unknown plot")
    try:
        from mas.lab.plots.providers import render_draw_module

        return Response(
            content=render_draw_module(kind),
            media_type="text/javascript",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"unknown plot {kind}") from exc


@router.get("/api/plots/{kind}", response_model=None)
async def plot_html(
    kind: str,
    request: Request,
    format: str = "embedded",
    mode: str = "",
    _: None = Depends(authorize_http),
):
    if not kind or "/" in kind or kind.startswith("."):
        raise HTTPException(status_code=404, detail="unknown plot")
    try:
        info = _plot_info(kind)
        requested = str(mode or format or "embedded").lower()
        standalone = requested in {"html", "htm", "svg", "png"}
        if not standalone:
            from mas.lab.plots.providers import render_embedded

            body = render_embedded(str(info.get("kind") or kind))
            return JSONResponse(
                {**info, **body, "mode": "embedded"},
                headers={
                    "X-Mas-Plot-Stream": info["stream"],
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
        fmt = requested
        body = _render_plot_html(str(info.get("kind") or kind), fmt=fmt)
        headers = {
            "X-Mas-Plot-Stream": info["stream"],
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        }
        if fmt == "svg":
            return Response(content=body, media_type="image/svg+xml", headers=headers)
        if fmt == "png":
            return Response(content=body, media_type="image/png", headers=headers)
        return HTMLResponse(body, headers=headers)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"unknown plot {kind}") from exc


@router.get("/api/plugins")
async def list_ui_plugins(
    kind: str | None = None,
    format: str | None = None,
    realtime: bool | None = None,
) -> dict[str, Any]:
    from mas.lab.controller.ui_stream.plugin_registry import list_plugins

    return {"plugins": list_plugins(kind=kind, format=format, realtime=realtime)}


@router.get("/api/layouts")
async def list_layouts() -> dict[str, Any]:
    from mas.lab.controller.ui_stream.layouts import list_layout_catalog

    catalog = list_layout_catalog(layout_roots())
    return {"layouts": [row["slug"] for row in catalog], "catalog": catalog}


@router.get("/api/layouts/{name}")
async def get_layout(name: str) -> dict[str, Any]:
    try:
        doc = load_layout(layout_roots(), name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await _hydrate_specs(doc)
    except Exception:
        logger.exception("layout spec hydrate skipped")
    return doc


@router.get("/api/jobs/{job_id}")
@router.get("/api/ui/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    found = state.lookup_job(job_id)
    if not found:
        raise HTTPException(status_code=404, detail="unknown job")
    return found


@router.get("/api/trace")
async def get_trace(
    scenario: str | None = None,
    batch: str | None = None,
    run: str | None = None,
) -> dict[str, Any]:
    ref = ref_from_mapping({"scenario": scenario, "batch": batch, "run": run})
    rows = [item.as_dict() for item in state.bus.snapshot(("*",), ref)]
    buckets = split_envelopes(rows)
    return {
        "kind": "mas-lab.ui-trace",
        "version": 1,
        "ref": ref.as_dict(),
        "runtime": buckets.get("runtime") or [],
        "pipeline": buckets.get("pipeline") or [],
        "n": len(rows),
        "files": [
            {"path": "runtime.jsonl", "tree": "runtime", "n": len(buckets.get("runtime") or [])},
            {"path": "pipeline.jsonl", "tree": "pipeline", "n": len(buckets.get("pipeline") or [])},
        ],
    }


@router.get("/api/layouts/{name}/snapshot")
async def layout_snapshot(
    name: str,
    session: str | None = None,
    run: str | None = None,
    batch: str | None = None,
    experiment: str | None = None,
    scenario: str | None = None,
    test: str | None = None,
) -> dict[str, Any]:
    try:
        layout = load_layout(layout_roots(), name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ref = ref_from_mapping(layout.get("dataSource")).merge(
        {
            "session": session,
            "run": run,
            "batch": batch,
            "experiment": experiment,
            "scenario": scenario,
            "test": test,
        }
    )
    await _hydrate_specs(layout)
    events = [item.as_dict() | {"replay": True} for item in state.bus.snapshot(("*",), ref)]
    return {
        "ref": ref.as_dict(),
        "layout": layout,
        "events": events,
        "jobs": _local_jobs(),
        "sessions": (await sessions())["sessions"],
        "runs": _runs_payload(),
        "metrics": state.metrics.snapshot(ref),
        "spec": {},
        "catalog": _safe_catalog(ref),
    }


def _arm_replay(nonce: str | None, *, replay: bool) -> str | None:
    """Stamp a replay with a unique nonce. A new nonce clears the live view."""
    tagged = str(nonce or "").strip() or None
    if not replay:
        return tagged
    tagged = tagged or str(uuid.uuid4())
    if tagged == state._replay_nonce:
        return tagged
    for task in list(state._replay_tasks.values()):
        task.cancel()
    state._replay_tasks.clear()
    state.bus.clear()
    state.metrics.clear()
    state.governance.clear()
    state.ingest.clear()
    state._replay_nonce = tagged
    token = use_nonce(tagged)
    try:
        state.bus.publish(
            envelope(
                "view.reset",
                DataSourceRef(),
                "view",
                {"phase": "reset", "nonce": tagged},
                replay=True,
            )
        )
    finally:
        reset_nonce(token)
    return tagged


@router.post("/api/ingest")
async def ingest(body: IngestBody, _: None = Depends(authorize_http)) -> dict[str, Any]:
    ref = ref_from_mapping(body.ref)
    if _drop_ingest(ref, replay=body.replay):
        return {"ok": True, "n": 0, "dropped": True}
    state._closed_until_run = False
    records = list(body.records)
    if body.record:
        records.append(body.record)
    _guard_ingest(len(records))
    nonce = _arm_replay(str(body.nonce or "").strip() or None, replay=body.replay)
    speed = float(body.speed or 0.0)
    if speed > 0 and len(records) > 1:
        job_id = str(uuid.uuid4())
        job_ref = _stamp_ui(ref.merge({"run": ref.run or job_id}))
        _arm_job(job_id, job_ref)
        task = asyncio.create_task(
            _pump_events(job_id, job_ref, records, speed=speed, nonce=nonce, replay=body.replay),
            name=f"ingest-{job_id[:8]}",
        )
        _register_replay_task(job_id, task)
        return {"ok": True, "n": len(records), "job_id": job_id, "speed": speed, "nonce": nonce}
    token = use_nonce(nonce) if nonce else None
    try:
        _seed_spec_from_records(ref, records)
        for record in records:
            rec_ref = ref
            if not ref.scenario and isinstance(record, dict) and record.get("scenario"):
                rec_ref = ref.merge({"scenario": record.get("scenario")})
            state.ingest.ingest(rec_ref, record, replay=body.replay)
            _index_native(rec_ref, record)
        return {"ok": True, "n": len(records), "nonce": nonce}
    finally:
        if token is not None:
            reset_nonce(token)


@router.post("/api/ingest/aligned")
async def ingest_aligned(body: AlignedIngestBody, _: None = Depends(authorize_http)) -> dict[str, Any]:
    """Replay several traces on one clock, aligned by event timestamp."""
    lanes: list[tuple[DataSourceRef, list[dict[str, Any]]]] = []
    total = 0
    for lane in body.lanes:
        ref = _stamp_ui(ref_from_mapping(lane.ref))
        records = list(lane.records or [])
        if not records:
            continue
        lanes.append((ref, records))
        total += len(records)
    if not lanes:
        return {"ok": True, "n": 0}
    _guard_ingest(total)
    job_id = str(uuid.uuid4())
    speed = float(body.speed)
    _arm_job(job_id, lanes[0][0])
    task = asyncio.create_task(
        _pump_aligned(job_id, lanes, speed=speed),
        name=f"align-{job_id[:8]}",
    )
    _register_replay_task(job_id, task)
    return {"ok": True, "n": total, "job_id": job_id, "speed": speed, "lanes": len(lanes)}


@router.post("/api/ingest/envelopes")
async def ingest_envelopes(body: EnvelopeIngestBody, _: None = Depends(authorize_http)) -> dict[str, Any]:
    """Replay a recorded WS/pipeline trace (runtime and/or pipeline trees)."""
    wanted = {name.strip() for name in (body.trees or []) if str(name).strip()}
    lanes: list[list] = []
    total = 0
    for lane in body.lanes:
        tree = str(lane.tree or "runtime")
        if wanted and tree not in wanted:
            continue
        rows = list(lane.envelopes or [])
        if not rows:
            continue
        lanes.append(envelopes_to_scheduled(rows, tree=tree))
        total += len(rows)
    if not lanes:
        return {"ok": True, "n": 0}
    _guard_ingest(total)
    job_id = str(uuid.uuid4())
    first = lanes[0][0]
    ref = ref_from_mapping((first.envelope or {}).get("ref"))
    _arm_job(job_id, _stamp_ui(ref))
    task = asyncio.create_task(
        _pump_envelopes(
            job_id,
            merge_timeline(lanes),
            speed=float(body.speed),
            recompute=body.recompute,
            cached_artefacts=tuple(body.cached_artefacts or ()),
        ),
        name=f"env-{job_id[:8]}",
    )
    _register_replay_task(job_id, task)
    return {"ok": True, "n": total, "job_id": job_id, "speed": body.speed, "recompute": body.recompute}


@router.post("/api/stream")
async def post_stream(body: StreamFrameBody, _: None = Depends(authorize_http)) -> dict[str, Any]:
    await _accept_stream_frame(body.model_dump())
    return {"ok": True}


@router.post("/api/commands")
async def commands(body: CommandBody, _: None = Depends(authorize_http)) -> dict[str, Any]:
    nonce = str(body.nonce or "").strip() or None
    token = use_nonce(nonce) if nonce else None
    try:
        return await _commands(body)
    finally:
        if token is not None:
            reset_nonce(token)


async def _commands(body: CommandBody) -> dict[str, Any]:
    ref = ref_from_mapping(body.ref)
    if body.batch:
        ref = ref.merge({"batch": body.batch})
    state.bus.publish(envelope(f"command.{body.verb.strip()}", ref, "command", body.model_dump()))
    verb = body.verb.strip()
    if verb in {"delete-run", "delete-session", "delete-ui-runs", "delete-all", "reset-view"}:
        if verb == "reset-view":
            result = await _delete_all_ui_runs()
            tombstones = set(state._deleted)
            state.reset()
            state._deleted.update(tombstones)
            result = {**result, "verb": "reset-view"}
        elif verb in {"delete-ui-runs", "delete-all"}:
            result = await _delete_all_ui_runs()
        else:
            result = await _delete_ui_run(ref)
        state.bus.publish(envelope(f"result.{verb}", ref, "result", {"results": [result]}))
        return {"results": [result]}
    if verb in {"run-experiment", "experiment"} and (ref.mode or "live") != "replay":
        await _publish_spec_snapshots(ref, body)
        try:
            result = await _start_live_experiment(body, ref)
        except HTTPException as exc:
            results = [{"ok": False, "detail": exc.detail}]
        else:
            results = [result]
        state.bus.publish(envelope(f"result.{verb}", ref, "result", {"results": results}))
        return {"results": results}
    raw_targets = list(body.targets) if body.targets else [body.ref]
    unique: list[DataSourceRef] = []
    seen: set[str] = set()
    for item in raw_targets:
        ref_item = ref.merge(ref_from_mapping(item if isinstance(item, dict) else {}))
        key = ref_item.coordinate() + "|" + (ref_item.overlay or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref_item)
    for item_ref in unique:
        await _publish_spec_snapshot(item_ref)
    gathered = await asyncio.gather(
        *[_dispatch_command(body, item_ref) for item_ref in unique],
        return_exceptions=True,
    )
    results: list[Any] = []
    for item in gathered:
        if isinstance(item, HTTPException):
            results.append({"ok": False, "detail": item.detail})
        elif isinstance(item, Exception):
            results.append({"ok": False, "detail": str(item)})
        else:
            results.append(item)
    state.bus.publish(envelope(f"result.{body.verb.strip()}", ref, "result", {"results": results}))
    return {"results": results}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    sub = None
    try:
        hello = json.loads(await ws.receive_text())
        if not isinstance(hello, dict):
            await ws.send_json({"error": "bad hello"})
            await ws.close(code=4400)
            return
        try:
            authorize_ws(ws, hello)
        except HTTPException:
            await ws.send_json({"error": "unauthorized"})
            await ws.close(code=4401)
            return
        raw_topics = hello.get("topics")
        from mas.lab.controller.ui_stream.streams import expand_stream_names

        get_topics = expand_stream_names(hello.get("get") or [])
        sub_topics = expand_stream_names(hello.get("subscribe") or raw_topics or [])
        if not sub_topics and raw_topics is None and not get_topics:
            sub_topics = ["*"]
        topics = list(dict.fromkeys([*get_topics, *sub_topics]))
        ref = ref_from_mapping(hello.get("ref"))
        raw_inspect = hello.get("inspect")
        inspect_ids = (
            tuple(raw_inspect) if isinstance(raw_inspect, list) else tuple(infer_inspect(topics))
        )
        nonce = str(hello.get("nonce") or "").strip() or None
        sub = state.bus.subscribe(sub_topics or topics, ref, nonce=nonce)
        state._inspect_ids[id(sub)] = inspect_ids
        catchup_topics = list(dict.fromkeys([*(get_topics or []), *(sub_topics or [])]))
        for item in state.bus.catchup(catchup_topics, ref, nonce=nonce):
            payload = item.as_dict()
            payload["replay"] = True
            await ws.send_json(payload)

        async def outbound() -> None:
            assert sub is not None
            while True:
                envelope_item = await sub.queue.get()
                await ws.send_json(envelope_item.as_dict())

        async def inbound() -> None:
            while True:
                data = await ws.receive_json()
                await _accept_stream_frame(data if isinstance(data, dict) else {})

        await asyncio.gather(outbound(), inbound())
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("websocket closed")
    finally:
        if sub is not None:
            state._inspect_ids.pop(id(sub), None)
            state.bus.unsubscribe(sub)


async def _accept_stream_frame(raw: dict[str, Any]) -> None:
    frame = parse_frame(raw)
    if is_command_frame(frame, raw):
        body = CommandBody.model_validate(command_payload(frame, raw))
        await commands(body)
        return
    if frame.source in {"native", "telemetry"} and isinstance(frame.payload, dict):
        if not _drop_ingest(frame.ref, replay=frame.replay):
            state.ingest.ingest(frame.ref, frame.payload, replay=frame.replay)
        return
    state.bus.publish(frame)


def _job_payload(job_id: str, status: str, ref: DataSourceRef, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "id": job_id,
        "job_id": job_id,
        "status": status,
        "origin": UI_ORIGIN,
        "library": ref.library,
        "experiment": ref.experiment,
        "scenario": ref.scenario,
        "test": ref.test,
        "run": ref.run or job_id,
        "session": ref.session,
        "overlay": ref.overlay,
        "streams": _streams(),
        "ws": "/ws",
        "mode": ref.mode or "live",
    }
    if extra:
        row.update(extra)
    return row


async def _dispatch_command(body: CommandBody, ref: DataSourceRef) -> dict[str, Any]:
    verb = body.verb.strip()
    mode = ref.mode or "live"
    if verb in CONTROL_VERBS:
        if not ref.session:
            raise HTTPException(status_code=400, detail="needs: session")
        return await _control(verb, body, ref)
    if verb in {"replay"} or mode == "replay" or verb in {"run", "run-mas", "submit"} and mode == "replay":
        return await _start_replay(body, ref)
    if verb in {"run", "run-mas", "submit"}:
        return await _start_live_mas(body, ref)
    if verb in {"run-experiment", "experiment"}:
        return await _start_live_experiment(body, ref)
    raise HTTPException(status_code=400, detail=f"unknown verb {verb!r}")


async def _control(verb: str, body: CommandBody, ref: DataSourceRef) -> dict[str, Any]:
    from mas.library.standard.plugins.control.attach import attach
    from mas.library.standard.plugins.control.directory import FileSessionDirectory

    directory = FileSessionDirectory(_session_dir())
    args: dict[str, Any] = {}
    if verb == "pause":
        args["reason"] = body.reason
    if verb in {"steer", "enqueue_input", "send_message"}:
        args["text"] = body.text or body.query
    if verb == "steer":
        args["mode"] = body.mode
    if verb == "navigate":
        args["to"] = body.to
        args["reason"] = body.reason
    client = await attach(directory, str(ref.session))
    try:
        result = await client.call(verb, str(ref.session), **args)
    finally:
        await client.close()
    state.bus.publish(envelope(f"control.{verb}", ref, "control", {"result": result, "args": args}))
    return {"ok": True, "verb": verb, "result": result}


async def _start_replay(body: CommandBody, ref: DataSourceRef) -> dict[str, Any]:
    if not ref.library:
        raise HTTPException(status_code=400, detail="needs: library")
    lib_dir = _lib_dir(ref.library)
    record = resolve_run(lib_dir, ref, indexed=state._indexed)
    if record.events is None or not record.events.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no events artefact for {ref.coordinate()}",
        )
    native = list(
        demo_records(
            parse_jsonl(record.events.read_text(encoding="utf-8")),
            extra_kinds=state.inspect_native_kinds(ref),
        )
    )
    records = merge_replay_records(
        native,
        trace_metric_records(
            metrics=record.artefacts.get("metrics"),
            progress=record.artefacts.get("progress"),
            events=native,
        ),
        *[
            custom_panel_records(path)
            for key, path in record.artefacts.items()
            if key.startswith("custom_")
        ],
    )
    if not records:
        raise HTTPException(status_code=400, detail="no demo events in run artefact")
    job_id = str(uuid.uuid4())
    job_ref = _stamp_ui(ref.merge({"run": ref.run or job_id, "mode": "replay"}))
    job = _job_payload(job_id, "running", job_ref, {"command": "replay"})
    state._closed_until_run = False
    state._job_refs[job_id] = job_ref
    state._job_seen[job_id] = "running"
    state._indexed[job_id] = record
    state._indexed[job_ref.coordinate()] = record
    _remember_run(job, job_ref)
    state.bus.publish(envelope("job.started", job_ref, "job", job))
    await _publish_spec_snapshot(job_ref)
    speed = float(body.speed)
    task = asyncio.create_task(
        _pump_events(
            job_id,
            job_ref,
            records,
            speed=speed,
            nonce=str(body.nonce or "").strip() or None,
            replay=True,
        ),
        name=f"replay-{job_id[:8]}",
    )
    _register_replay_task(job_id, task)
    return {"ok": True, "verb": body.verb.strip(), "job": job, "streams": _streams(), "ws": "/ws"}


def _seed_spec_from_records(
    ref: DataSourceRef,
    records: list[dict[str, Any]],
    *,
    base: dict[str, Any] | None = None,
) -> None:
    """Publish topology spec before native events so the graph is not grown from turns."""
    from mas.lab.controller.ui_stream.spec_snapshot import merge_spec_snapshots, snapshot_from_records

    inferred = snapshot_from_records(records)
    snap = merge_spec_snapshots(base or {}, inferred)
    if not snap.get("agents"):
        return
    state.bus.publish(envelope("spec.snapshot", ref, "spec", snap))
    state.ingest.seed_spec(ref, snap)


async def _seed_replay_spec(ref: DataSourceRef, records: list[dict[str, Any]]) -> None:
    """Always publish spec (and seed topology) before the first event of a replay."""
    base: dict[str, Any] = {}
    if ref.library:
        try:
            base = snapshot_for_ref(_lib_dir(ref.library), experiment=ref.experiment, overlay=ref.overlay) or {}
        except Exception:
            base = {}
        if base.get("agents"):
            state.bus.publish(envelope("spec.snapshot", ref, "spec", base))
            state.ingest.seed_spec(ref, base)
    _seed_spec_from_records(ref, records, base=base)


def _seal_live_artefacts(ref: DataSourceRef) -> None:
    """Mark each produced live fold complete so replay can skip it."""
    state.ingest.seal_artefacts(ref)


async def _finish_job(job_id: str, ref: DataSourceRef, extra: dict[str, Any] | None = None, *, status: str = "completed") -> None:
    job = _job_payload(job_id, status, ref, extra or {"command": "replay"})
    state._job_seen[job_id] = status
    _remember_run(job, ref)
    if status == "completed":
        _seal_live_artefacts(ref)
    _dump_job_trace(job_id, ref)
    state.bus.publish(envelope("job.completed", ref, "job", job, clock="server", complete=True))


def _live_fold_lanes() -> list[dict[str, Any]]:
    return [
        {"name": "runtime", "source": "file", "tree": "runtime"},
        {"name": "chat", "source": "live", "fold": "LivePlotFolds"},
        {"name": "topology", "source": "live", "fold": "LivePlotFolds"},
        {"name": "series", "source": "live", "fold": "LivePlotFolds"},
    ]


def _is_chat_snapshot(env: dict[str, Any] | None) -> bool:
    blob = env or {}
    topic = str(blob.get("topic") or blob.get("artefact") or "")
    if "custom.chat" in topic:
        return True
    payload = blob.get("payload") if isinstance(blob.get("payload"), dict) else {}
    kind = str(payload.get("kind") or blob.get("kind") or "")
    return kind.startswith("custom.chat") or kind == "chat"


def _envelope_lanes(
    items,
    *,
    recompute: bool = False,
    cached_artefacts: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    trees: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        name = str(getattr(item, "tree", "") or "runtime")
        if name in seen:
            continue
        seen.add(name)
        trees.append(name)
    out: list[dict[str, Any]] = []
    held = {str(name) for name in cached_artefacts if str(name).strip()}
    held.discard("custom.chat")
    for tree in trees:
        out.append({"name": tree, "source": "file", "tree": tree})
        if tree == "runtime":
            out.append({"name": "chat", "source": "live", "fold": "LivePlotFolds"})
        if recompute and tree == "runtime":
            for name, artefact, fold in (
                ("topology", "custom.topology", "LivePlotFolds"),
                ("series", "series", "LivePlotFolds"),
            ):
                cached = any(artefact in token for token in held)
                if cached:
                    out.append({"name": name, "source": "file", "tree": "pipeline", "path": artefact})
                else:
                    out.append({"name": name, "source": "live", "fold": fold})
    return out or _live_fold_lanes()


def _seed_job_progress(job_id: str, items, lanes: list[dict[str, Any]] | None) -> None:
    if not items:
        state._job_progress[job_id] = {"t": 0, "t0": 0, "t1": 0, "n": 0, "n_total": 0, "lanes": lanes or []}
        return
    t0 = items[0].ts
    t1 = items[-1].ts
    trees: dict[str, dict[str, int]] = {}
    for item in items:
        slot = trees.setdefault(item.tree, {"n": 0, "n_total": 0})
        slot["n_total"] += 1
    state._job_progress[job_id] = {
        "t": t0,
        "t0": t0,
        "t1": t1,
        "n": 0,
        "n_total": len(items),
        "lanes": lanes or [],
        "trees": trees,
    }


def _publish_job_clock(job_id: str, ref: DataSourceRef, *, speed: float) -> None:
    payload = dict(state._job_progress.get(job_id) or {})
    payload["speed"] = float(speed)
    state._job_progress[job_id] = payload
    state.bus.publish(envelope("job.progress", ref, "job", payload, replay=True, ts=payload.get("t")))


def _on_job_progress(
    job_id: str,
    lanes: list[dict[str, Any]] | None,
    *,
    ref: DataSourceRef | None = None,
    speed: float = 1.0,
):
    last_pub = {"n": -1, "wall": 0.0}

    def on_progress(prog: dict[str, Any]) -> None:
        payload = {**prog, "lanes": lanes or prog.get("lanes") or [], "speed": float(speed)}
        state._job_progress[job_id] = payload
        if ref is None:
            return
        n = int(prog.get("n") or 0)
        n_total = int(prog.get("n_total") or 0)
        wall = datetime.now(UTC).timestamp()
        if n != n_total and n - int(last_pub["n"]) < 8 and wall - float(last_pub["wall"]) < 0.12:
            return
        last_pub["n"] = n
        last_pub["wall"] = wall
        state.bus.publish(
            envelope("job.progress", ref, "job", payload, replay=True, ts=prog.get("t"))
        )

    return on_progress


async def _pump_events(
    job_id: str,
    ref: DataSourceRef,
    records: list[dict[str, Any]],
    *,
    speed: float = 1.0,
    nonce: str | None = None,
    replay: bool = False,
) -> None:
    token = use_clock("server")
    nonce_token = use_nonce(nonce) if nonce else None
    try:
        await _seed_replay_spec(ref, records)
        items = records_to_scheduled(records, ref=ref, tree="runtime")
        lanes = _live_fold_lanes()
        _seed_job_progress(job_id, items, lanes)
        _publish_job_clock(job_id, ref, speed=speed)

        def emit(item):
            state.ingest.ingest(ref, item.record, replay=replay)

        await pump_timeline(
            items,
            speed=speed,
            emit=emit,
            cancelled=lambda: job_id in state._deleted,
            on_progress=_on_job_progress(job_id, lanes, ref=ref, speed=speed),
        )
        state.ingest.flush_snapshot(ref, replay=replay)
        await _finish_job(job_id, ref)
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.exception("events replay failed")
        await _finish_job(job_id, ref, {"error": str(exc)}, status="failed")
    finally:
        reset_clock(token)
        if nonce_token is not None:
            reset_nonce(nonce_token)
        state._replay_tasks.pop(job_id, None)


async def _pump_aligned(
    job_id: str,
    lanes: list[tuple[DataSourceRef, list[dict[str, Any]]]],
    *,
    speed: float = 1.0,
) -> None:
    first_ref = lanes[0][0]
    token = use_clock("server")
    try:
        scheduled = []
        for ref, records in lanes:
            await _seed_replay_spec(ref, records)
            scheduled.append(records_to_scheduled(records, ref=ref, tree="runtime"))
        items = merge_timeline(scheduled)
        lanes = _live_fold_lanes()
        _seed_job_progress(job_id, items, lanes)
        _publish_job_clock(job_id, first_ref, speed=speed)

        def emit(item):
            state.ingest.ingest(item.ref, item.record, replay=False)

        await pump_timeline(
            items,
            speed=speed,
            emit=emit,
            cancelled=lambda: job_id in state._deleted,
            on_progress=_on_job_progress(job_id, lanes, ref=first_ref, speed=speed),
        )
        for ref, _records in lanes:
            state.ingest.flush_snapshot(ref, replay=False)
        await _finish_job(job_id, first_ref, {"command": "replay", "align": "time"})
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.exception("aligned replay failed")
        await _finish_job(job_id, first_ref, {"error": str(exc)}, status="failed")
    finally:
        reset_clock(token)
        state._replay_tasks.pop(job_id, None)


async def _pump_envelopes(
    job_id: str,
    items,
    *,
    speed: float = 1.0,
    recompute: bool = False,
    cached_artefacts: tuple[str, ...] = (),
) -> None:
    first_ref = DataSourceRef()
    if items:
        env = items[0].envelope or {}
        first_ref = ref_from_mapping(env.get("ref") if isinstance(env.get("ref"), dict) else {})
    token = use_clock("server")
    held = {name.strip() for name in cached_artefacts if str(name).strip()}
    held.discard("custom.chat")
    has_runtime = any(str(getattr(item, "tree", "") or "") == "runtime" for item in items or [])
    state.ingest._skip_plots = set(held)
    lanes = _envelope_lanes(items, recompute=recompute, cached_artefacts=tuple(held))
    _seed_job_progress(job_id, items, lanes)
    _publish_job_clock(job_id, first_ref, speed=speed)
    try:
        def emit(item):
            env = item.envelope or {}
            if has_runtime and str(getattr(item, "tree", "") or "") == "pipeline" and _is_chat_snapshot(env):
                return
            ref = ref_from_mapping(env.get("ref") if isinstance(env.get("ref"), dict) else {})
            payload = env.get("payload")
            if recompute and item.tree == "runtime" and isinstance(payload, dict) and payload.get("kind"):
                state.ingest.ingest(ref, payload, replay=False)
                return
            state.bus.publish(envelope_from_mapping(env))
            if (
                has_runtime
                and item.tree == "runtime"
                and isinstance(payload, dict)
                and payload.get("kind")
            ):
                def _emit_chat(target: DataSourceRef, rec: dict[str, Any]) -> None:
                    plot_kind = str(rec.get("kind") or "")
                    if not plot_kind.startswith("custom.chat"):
                        return
                    state.bus.publish(
                        envelope(plot_kind, target, "custom", rec, replay=False, complete=False)
                    )

                state.ingest._plots.apply(
                    _emit_chat, ref, payload, skip={"custom.topology", "series"}
                )

        await pump_timeline(
            items,
            speed=speed,
            emit=emit,
            cancelled=lambda: job_id in state._deleted,
            on_progress=_on_job_progress(job_id, lanes, ref=first_ref, speed=speed),
        )
        if recompute:
            state.ingest.flush_snapshot(first_ref, replay=False)
        await _finish_job(
            job_id,
            first_ref,
            {"command": "replay", "envelopes": True, "recompute": recompute, "cached": sorted(held)},
        )
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.exception("envelope replay failed")
        await _finish_job(job_id, first_ref, {"error": str(exc)}, status="failed")
    finally:
        state.ingest._skip_plots = set()
        reset_clock(token)
        state._replay_tasks.pop(job_id, None)


async def _start_live_experiment(body: CommandBody, ref: DataSourceRef) -> dict[str, Any]:
    from mas.lab.controller.api import get_controller_api
    from mas.lab.controller.routes.experiments import resolve_experiment_path
    from mas.lab.controller.routes.run import _run_pre_steps

    if not ref.library:
        raise HTTPException(status_code=400, detail="needs: library")
    experiment = body.experiment or ref.experiment
    if not experiment:
        raise HTTPException(status_code=400, detail="needs: experiment")
    lib_dir = _lib_dir(ref.library)
    pre = list(body.pre or [])
    for item in body.targets or []:
        if isinstance(item, dict):
            pre.extend(item.get("pre") or [])
    _run_pre_steps(lib_dir, pre)
    experiment_path = resolve_experiment_path(lib_dir, experiment)
    ingest = os.environ.get("MAS_DEMO_UI_INGEST_URL") or _controller_ingest_url()
    target_refs = [ref.merge(item) if isinstance(item, dict) else ref for item in (body.targets or [])]
    if not target_refs:
        target_refs = [ref]
    infra_refs, scenario_infra = _live_infra(lib_dir, target_refs)
    scenario_overlays = _live_overlays(target_refs)
    spec = {
        "experiment_yaml": str(experiment_path),
        "progress": False,
        "live": True,
        "force": True,
        "query": body.query or body.text,
        "cwd": str(lib_dir),
        "infra_refs": infra_refs,
        "scenario_infra_refs": scenario_infra,
        "scenario_overlays": scenario_overlays,
        "demo_ui_ingest_url": ingest,
        "demo_ui_ref": {k: v for k, v in _stamp_ui(ref).as_dict().items() if k != "scenario"},
        "pre": pre,
        "endpoint": f"/api/libraries/{ref.library}/experiment/run",
        "command": f"mas-lab experiment run {experiment_path.name}",
    }
    os.environ.setdefault("MAS_DEMO_UI_INGEST_KINDS", "all")
    state._closed_until_run = False
    result = get_controller_api().submit_experiment(spec)
    job_id = str(result.get("job_id") or result.get("worker_id") or "")
    job_ref = _stamp_ui(ref.merge({"run": job_id, "experiment": experiment, "mode": "live"}))
    job = _job_payload(job_id, str(result.get("status") or "running"), job_ref, {"command": spec["command"]})
    state._closed_until_run = False
    if job_id:
        state._job_refs[job_id] = job_ref
        state._job_seen[job_id] = job["status"]
        _remember_run(job, job_ref)
    state.bus.publish(envelope("job.started", job_ref, "job", job))
    await _publish_spec_snapshots(job_ref, body)
    return {"ok": True, "verb": body.verb.strip(), "job": job, "streams": _streams(), "ws": "/ws"}


async def _start_live_mas(body: CommandBody, ref: DataSourceRef) -> dict[str, Any]:
    from mas.lab.controller.models import RunMASRequest
    from mas.lab.controller.routes.run import run_mas

    if not ref.library:
        raise HTTPException(status_code=400, detail="needs: library")
    lib_dir = _lib_dir(ref.library)
    mas_rel = infer_mas_rel(lib_dir, ref.experiment)
    if not mas_rel:
        raise HTTPException(status_code=400, detail="needs: experiment (to infer MAS)")
    overlays = body.overlays or ([ref.overlay] if ref.overlay else [])
    ingest = os.environ.get("MAS_DEMO_UI_INGEST_URL") or _controller_ingest_url()
    os.environ.setdefault("MAS_DEMO_UI_INGEST_KINDS", "all")
    state._closed_until_run = False
    req = RunMASRequest(
        query=body.query or body.text,
        mas_path=mas_rel,
        overlays=overlays,
        demo_ui_ingest_url=ingest,
        demo_ui_ref=_stamp_ui(ref).as_dict(),
        timeout=body.timeout or 90,
    )
    job = await run_mas(ref.library, req)
    job_id = str(job.get("job_id") or "")
    job_ref = _stamp_ui(ref.merge({"run": job_id, "session": job.get("session_id"), "mode": "live"}))
    payload = _job_payload(job_id, str(job.get("status") or "running"), job_ref)
    payload.update(job)
    state._closed_until_run = False
    if job_id:
        state._job_refs[job_id] = job_ref
        state._job_seen[job_id] = payload["status"]
        _remember_run(payload, job_ref)
    state.bus.publish(envelope("job.started", job_ref, "job", payload))
    await _publish_spec_snapshot(job_ref)
    return {"ok": True, "verb": body.verb.strip(), "job": payload, "streams": _streams(), "ws": "/ws"}


async def _publish_spec_snapshots(ref: DataSourceRef, body: CommandBody | None = None) -> None:
    targets: list[DataSourceRef] = []
    if body is not None:
        for item in body.targets or []:
            if isinstance(item, dict):
                targets.append(ref.merge(item))
        for overlay in body.overlays or []:
            targets.append(ref.merge({"overlay": str(overlay)}))
    if ref.overlay or ref.scenario:
        targets.append(ref)
    uniq: list[DataSourceRef] = []
    seen: set[tuple[str | None, str | None, str | None, str | None]] = set()
    for item in targets:
        key = (item.library, item.experiment, item.scenario, item.overlay)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    if not uniq:
        await _publish_spec_snapshot(ref)
        return
    for item in uniq:
        await _publish_spec_snapshot(item)


async def _publish_spec_snapshot(ref: DataSourceRef) -> None:
    if not ref.library:
        return
    try:
        lib_dir = _lib_dir(ref.library)
        snap = snapshot_for_ref(lib_dir, experiment=ref.experiment, overlay=ref.overlay)
        state.bus.publish(envelope("spec.snapshot", ref, "spec", snap))
        state.ingest.seed_spec(ref, snap)
    except HTTPException as exc:
        logger.warning("spec snapshot skipped: %s", exc.detail)
        state.bus.publish(envelope("spec.error", ref, "spec", {"error": str(exc.detail)}))
    except FileNotFoundError as exc:
        logger.warning("spec snapshot skipped: %s", exc)
        state.bus.publish(envelope("spec.error", ref, "spec", {"error": str(exc)}))
    except Exception as exc:
        logger.exception("spec snapshot failed")
        state.bus.publish(envelope("spec.error", ref, "spec", {"error": str(exc)}))


async def _hydrate_specs(layout: dict[str, Any]) -> None:
    found: list[DataSourceRef] = []

    def walk(node: Any, inherited: DataSourceRef) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, inherited)
            return
        if not isinstance(node, dict):
            return
        current = inherited
        ds = node.get("dataSource")
        if isinstance(ds, dict):
            current = inherited.merge(ds)
            found.append(current)
        walk(node.get("children"), current)
        walk(node.get("content"), current)
        walk(node.get("layout"), current)
        walk(node.get("leftPanel"), current)
        walk(node.get("rightPanel"), current)

    walk(layout, DataSourceRef())
    seen: set[tuple[str | None, str | None, str | None, str | None]] = set()
    for item in found:
        key = (item.library, item.experiment, item.scenario, item.overlay)
        if key in seen or not item.library:
            continue
        seen.add(key)
        await _publish_spec_snapshot(item)


def _index_native(ref: DataSourceRef, record: dict[str, Any]) -> None:
    if ref.run:
        state._indexed.setdefault(
            ref.coordinate() or ref.run,
            RunRecord(ref=ref),
        )


def _drop_ingest(ref: DataSourceRef, *, replay: bool) -> bool:
    if replay:
        return False
    if state._closed_until_run:
        return True
    return bool(ref.run and ref.run in state._deleted)


def _fold_run_artefact_metrics(ref: DataSourceRef) -> None:
    """Phase 1: after a v1 post pipeline, publish cached metrics.json onto the bus."""
    if not ref.library:
        return
    try:
        lib_dir = _lib_dir(ref.library)
    except Exception:
        return
    scenarios = [ref.scenario] if ref.scenario else ["baseline"]
    for scenario in scenarios:
        if not scenario:
            continue
        scoped = ref.merge({"scenario": scenario})
        try:
            record = resolve_run(lib_dir, scoped, indexed=state._indexed)
        except Exception:
            continue
        from mas.lab.controller.ui_stream.replay import (
            metric_record_from_artefact,
            metric_records_from_progress,
            parse_jsonl,
            stamp_artefact_metrics,
        )

        extras: list[dict[str, Any]] = []
        progress = record.artefacts.get("progress")
        if progress is not None:
            extras.extend(metric_records_from_progress(progress))
        metrics_path = record.artefacts.get("metrics")
        artefact = metric_record_from_artefact(metrics_path) if metrics_path is not None else None
        if artefact is not None and record.events is not None and record.events.is_file():
            events = parse_jsonl(record.events.read_text(encoding="utf-8"))
            artefact = stamp_artefact_metrics(artefact, events)
        if artefact is not None:
            extras.append(artefact)
        for item in extras:
            state.ingest.ingest(scoped, item, replay=False)


def _remember_run(job: dict[str, Any], ref: DataSourceRef) -> None:
    job_id = str(job.get("id") or job.get("job_id") or ref.run or "")
    if not job_id or job_id in state._deleted:
        return
    row = {
        "id": job_id,
        "run": ref.run or job_id,
        "batch": ref.batch,
        "session": str(job.get("session_id") or ref.session or "") or None,
        "status": str(job.get("status") or ""),
        "library": ref.library,
        "overlay": ref.overlay,
        "experiment": ref.experiment,
        "scenario": ref.scenario,
        "test": ref.test,
        "mode": ref.mode,
        "ts": datetime.now(UTC).isoformat(),
        "origin": ref.origin or UI_ORIGIN,
        "label": " · ".join(part for part in (ref.overlay or ref.scenario or ref.library or "run", job_id[:8]) if part),
        "streams": _streams(),
    }
    state._runs = [item for item in state._runs if item.get("id") != job_id]
    state._runs.append(row)
    if len(state._runs) > 40:
        state._runs = state._runs[-40:]


def _runs_payload() -> list[dict[str, Any]]:
    by_id = {
        item["id"]: item
        for item in state._runs
        if item.get("id") and item.get("origin", UI_ORIGIN) == UI_ORIGIN and item.get("id") not in state._deleted
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in reversed(list(by_id.values())):
        gid = str(row.get("batch") or row["id"])
        group = grouped.get(gid)
        if group is None:
            group = {
                "id": gid,
                "batch": row.get("batch") or None,
                "jobs": [],
                "library": row.get("library"),
                "session": row.get("session"),
                "status": "",
                "label": gid[:8],
            }
            grouped[gid] = group
        group["jobs"].append(row)
    out: list[dict[str, Any]] = []
    for group in grouped.values():
        overlays = [str(job.get("overlay") or "") for job in group["jobs"] if job.get("overlay")]
        scenarios = [str(job.get("scenario") or "") for job in group["jobs"] if job.get("scenario")]
        stamp = str((group["jobs"][0] or {}).get("ts") or "")
        time = stamp.split("T", 1)[1][:5] if "T" in stamp else stamp[:16]
        names = scenarios or overlays
        short: list[str] = []
        for name in names:
            item = name.replace("-round-cap", "").removeprefix("with-").removesuffix("-entry")
            if item and item not in short:
                short.append(item)
        group["time"] = time
        group["overlays"] = overlays
        names = " / ".join(short) or group.get("library")
        group["label"] = " · ".join(part for part in (time, names) if part) or group["id"][:8]
        statuses = [str(job.get("status") or "") for job in group["jobs"]]
        if any(item in {"running", "pending"} for item in statuses):
            group["status"] = "running"
        elif any(item in {"failed", "cancelled", "timeout"} for item in statuses):
            group["status"] = "failed"
        elif statuses:
            group["status"] = statuses[0]
        out.append(group)
    return out


def _arm_job(job_id: str, ref: DataSourceRef, *, command: str = "ingest") -> None:
    state._job_seen[job_id] = "running"
    state._job_refs[job_id] = ref
    job = _job_payload(job_id, "running", ref, {"command": command})
    state.bus.publish(envelope("job.started", ref, "job", job, replay=True))


def _dump_job_trace(job_id: str, ref: DataSourceRef) -> None:
    dest_root = os.environ.get("MAS_DEMO_UI_TRACE_DIR")
    if not dest_root:
        return
    dest = Path(dest_root) / job_id
    rows = [item.as_dict() for item in state.bus.snapshot(("*",), DataSourceRef())]
    write_ui_trace(dest, rows, extra={"job_id": job_id, "ref": ref.as_dict()})


def _local_jobs() -> list[dict[str, Any]]:
    return [
        {
            "id": job_id,
            "status": state._job_seen.get(job_id, ""),
            "origin": UI_ORIGIN,
            **(state._job_refs[job_id].as_dict() if job_id in state._job_refs else {}),
        }
        for job_id in state._job_refs
        if job_id not in state._deleted
    ]


def _safe_catalog(ref: DataSourceRef) -> dict[str, Any]:
    from mas.lab.controller.deps import get_manifest_store

    store = get_manifest_store()
    out: dict[str, Any] = {"libraries": store.libraries()}
    if ref.library:
        try:
            out["experiments"] = store.list_experiments(ref.library)
        except Exception:
            out["experiments"] = []
        try:
            out["overlays"] = store.list_overlays(ref.library)
        except Exception:
            out["overlays"] = []
    return out


def _forget_run(job_id: str, *, tombstone: bool = True) -> None:
    task = state._replay_tasks.pop(job_id, None)
    if task is not None:
        task.cancel()
    state._job_refs.pop(job_id, None)
    state._job_seen.pop(job_id, None)
    state._job_progress.pop(job_id, None)
    state._runs = [item for item in state._runs if item.get("id") != job_id]
    if tombstone:
        state._deleted.add(job_id)


async def _delete_ui_run(ref: DataSourceRef) -> dict[str, Any]:
    from mas.lab.controller.api import peek_controller_api

    keys = {part for part in (ref.batch, ref.run, ref.session) if part}
    ids = [
        str(item["id"])
        for item in state._runs
        if item.get("id") in keys or item.get("batch") in keys
    ]
    for job_id, pinned in list(state._job_refs.items()):
        if job_id in keys or pinned.batch in keys or pinned.run in keys:
            ids.append(job_id)
    api = peek_controller_api()
    for job_id in dict.fromkeys(ids):
        if api is not None:
            api.drop_worker(job_id)
        _forget_run(job_id)
        state.bus.drop(run=job_id, batch=ref.batch)
        state.bus.publish(envelope("job.deleted", ref.merge({"run": job_id}), "job", {"id": job_id}))
    return {"ok": True, "verb": "delete-run", "removed": list(dict.fromkeys(ids))}


async def _delete_all_ui_runs() -> dict[str, Any]:
    from mas.lab.controller.api import peek_controller_api

    ids = list(dict.fromkeys([str(item.get("id") or "") for item in state._runs] + list(state._job_refs)))
    api = peek_controller_api()
    if api is not None:
        api.drop_workers(origin=UI_ORIGIN, terminal_only=False)
    removed: list[str] = []
    for job_id in ids:
        if not job_id:
            continue
        _forget_run(job_id)
        removed.append(job_id)
    state._runs = []
    state._job_refs.clear()
    state._job_seen.clear()
    state._job_progress.clear()
    state.bus.drop(origin=UI_ORIGIN)
    state.bus.publish(envelope("job.deleted", DataSourceRef(origin=UI_ORIGIN), "job", {"removed": removed}))
    return {"ok": True, "verb": "delete-ui-runs", "removed": list(dict.fromkeys(removed))}


async def _poll_loop() -> None:
    from mas.lab.controller.api import peek_controller_api

    while True:
        try:
            api = peek_controller_api()
            jobs = api.list_jobs() if api is not None else []
            for job in jobs:
                job_id = str(job.get("id") or "")
                if not job_id or job_id in state._deleted:
                    continue
                pinned = state._job_refs.get(job_id)
                if pinned is None and job.get("origin") != UI_ORIGIN:
                    continue
                status = str(job.get("status") or "")
                prev = state._job_seen.get(job_id)
                if prev == status:
                    continue
                state._job_seen[job_id] = status
                ref = pinned or DataSourceRef(run=job_id, origin=UI_ORIGIN)
                _remember_run(job, ref)
                if prev is None:
                    topic = "job.started"
                elif status in {"completed", "failed", "cancelled", "timeout"}:
                    topic = "job.completed"
                else:
                    topic = "job.status_changed"
                state.bus.publish(envelope(topic, ref, "job", job))
                if topic == "job.completed" and status == "completed":
                    _fold_run_artefact_metrics(ref)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ui stream poll failed")
        await asyncio.sleep(1.5)


@asynccontextmanager
async def ui_stream_lifespan(_app: FastAPI):
    state._poll_task = asyncio.create_task(_poll_loop(), name="ui-stream-poll")
    try:
        yield
    finally:
        await state.close()


_DEMO_UI_SKIP = frozenset({"api", "health", "ws", "docs", "redoc", "openapi.json", "json"})


def _demo_ui_dev_origin() -> str:
    return str(os.environ.get("MAS_DEMO_UI_DEV_ORIGIN") or "").rstrip("/")


def _esc_html(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _gallery_html(catalog: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for row in catalog:
        slug = _esc_html(row.get("slug") or "")
        title = _esc_html(row.get("title") or slug)
        group = _esc_html(row.get("group") or "")
        cards.append(
            '<a class="card" href="#'
            + slug
            + '"><span class="group">'
            + group
            + "</span><strong>"
            + title
            + "</strong><span class=\"slug\">"
            + slug
            + "</span></a>"
        )
    body = "\n".join(cards) or "<p>No layouts found.</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Demo layouts</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ margin: 0; font: 15px/1.45 ui-sans-serif, system-ui, sans-serif; background: #0b1220; color: #e5e7eb; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 2.5rem 1.25rem; }}
    h1 {{ font-size: 1.4rem; font-weight: 600; margin: 0 0 0.35rem; }}
    p {{ color: #94a3b8; margin: 0 0 1.5rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.85rem; }}
    .card {{ display: flex; flex-direction: column; gap: 0.3rem; padding: 0.9rem 1rem; border: 1px solid #1f2937; border-radius: 10px; background: #111827; color: inherit; text-decoration: none; }}
    .card:hover {{ border-color: #38bdf8; }}
    .card strong {{ font-size: 1rem; }}
    .group {{ font-size: 0.7rem; letter-spacing: 0.04em; text-transform: uppercase; color: #64748b; }}
    .slug {{ font: 0.75rem/1.3 ui-monospace, monospace; color: #94a3b8; }}
  </style>
</head>
<body>
  <main>
    <h1>Demo layouts</h1>
    <p>Pick an interface. Replay and live telemetry attach after it opens.</p>
    <div class="grid">{body}</div>
  </main>
</body>
</html>
"""


async def serve_demo_ui(request: Request, path: str) -> Response:
    origin = _demo_ui_dev_origin()
    if origin:
        import httpx

        url = f"{origin}/{path}" if path else f"{origin}/"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        headers = {key: value for key, value in request.headers.items() if key.lower() not in {"host", "connection"}}
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                upstream = await client.request(request.method, url, headers=headers)
        except httpx.HTTPError:
            upstream = None
        if upstream is not None:
            skip = {"content-encoding", "transfer-encoding", "connection", "content-length"}
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers={key: value for key, value in upstream.headers.items() if key.lower() not in skip},
                media_type=upstream.headers.get("content-type"),
            )
    from mas.lab.controller.ui_stream.layouts import list_layout_catalog

    return HTMLResponse(_gallery_html(list_layout_catalog(layout_roots())))


def mount_ui_stream(app: FastAPI) -> None:
    app.include_router(router)

    @app.middleware("http")
    async def demo_ui_security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in security_headers().items():
            response.headers.setdefault(key, value)
        return response

    @app.get("/", include_in_schema=False)
    async def demo_ui_index(request: Request) -> Response:
        return await serve_demo_ui(request, "")

    @app.get("/{path:path}", include_in_schema=False)
    async def demo_ui_asset(path: str, request: Request) -> Response:
        head = path.split("/", 1)[0]
        if head in _DEMO_UI_SKIP:
            raise HTTPException(status_code=404, detail="not found")
        return await serve_demo_ui(request, path)
