#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mas.lab.controller.ui_stream.app import mount_ui_stream, state
from mas.lab.controller.ui_stream.ingest import live_tick
from mas.lab.controller.ui_stream.layouts import load_layout
from mas.lab.controller.ui_stream.refs import DataSourceRef, envelope
from mas.lab.controller.ui_stream.run_index import overlay_rel, resolve_run
from mas.lab.controller.ui_stream.spec_load import snapshot_for_ref

def test_live_tick_drops_html_shells_keeps_chart_data() -> None:
    assert live_tick({"kind": "custom.chat.plot", "html": "<html></html>"}) is None
    kept = live_tick({"kind": "custom.panel-a.plot", "html": "<html></html>", "data": {"agents": [1]}})
    assert kept is not None
    assert "html" not in kept
    assert kept["data"] == {"agents": [1]}
    snap = live_tick({"kind": "custom.chat.snapshot", "cards": [{"text": "hi"}]})
    assert snap["cards"][0]["text"] == "hi"


JSONL = "\n".join(
    [
        '{"kind":"execution_start","agent_id":"coordinator","input":"hi","call_id":"1"}',
        '{"kind":"user_response","agent_id":"specialist","content":"ok","call_id":"2"}',
        '{"kind":"hitl_gate","agent_id":"coordinator","call_id":"3"}',
        '{"kind":"mas_call_end","agent_id":"mas","call_id":"0"}',
        "",
    ]
)


def _write_lab(root: Path) -> Path:
    lib = root / "demo-lib"
    (lib / "mas" / "agents").mkdir(parents=True)
    (lib / "mas" / "overlays").mkdir(parents=True)
    (lib / "infra" / ".cache" / "versions" / "s1" / "v4").mkdir(parents=True)
    (lib / "output" / "baseline" / "item1" / "r1" / "traces").mkdir(parents=True)
    (lib / "experiment.yaml").write_text(
        "experiment:\n  name: scenario-1-ab\n  applications:\n    - manifest: mas/scenario-1.mas.yaml\n",
        encoding="utf-8",
    )
    (lib / "mas" / "scenario-1.mas.yaml").write_text(
        "spec:\n  agency:\n    agents:\n      - {id: coordinator, ref: agents/coordinator.yaml}\n"
        "      - {id: specialist, ref: agents/specialist.yaml}\n"
        "  workflow:\n    entry: coordinator\n    nodes:\n"
        "      - {id: coordinator, delegates_to: [specialist]}\n",
        encoding="utf-8",
    )
    (lib / "mas" / "agents" / "coordinator.yaml").write_text(
        "spec:\n  tools: [{ref: tools/delegate.yaml}]\n  skills: []\n",
        encoding="utf-8",
    )
    (lib / "mas" / "agents" / "specialist.yaml").write_text(
        "spec:\n  tools: [lookup]\n  skills: [policy]\n",
        encoding="utf-8",
    )
    (lib / "mas" / "overlays" / "baseline-round-cap.yaml").write_text(
        "spec:\n  patch:\n    agents:\n      $entry:\n"
        "        skills:\n          $op:\n            add: [orchestrator]\n",
        encoding="utf-8",
    )
    cache = lib / "infra" / ".cache" / "versions" / "s1" / "v4"
    (cache / "baseline.events.jsonl").write_text(JSONL, encoding="utf-8")
    (cache / "arm-b.events.jsonl").write_text(
        '{"kind":"execution_start","agent_id":"coordinator","input":"from-gold","call_id":"8","timestamp":10}\n'
        '{"kind":"llm_call_end","agent_id":"coordinator","call_id":"9","timestamp":11,'
        '"tokens_used":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120,'
        '"prompt_tokens_details":{"cached_tokens":40},"cache_read_input_tokens":40}}\n'
        '{"kind":"mas_call_end","agent_id":"mas","call_id":"0","timestamp":13}\n',
        encoding="utf-8",
    )
    (cache / "arm-b.metrics.json").write_text(
        '{"scenario":"arm-b","session":{"goal_success_rate":{"value":0.8}}}\n',
        encoding="utf-8",
    )
    (lib / "infra" / ".cache" / "versions" / "s1" / "v4" / "baseline-round-cap.yaml").write_text(
        "spec: {}\n",
        encoding="utf-8",
    )
    (lib / "output" / "baseline" / "item1" / "r1" / "traces" / "events.jsonl").write_text(
        '{"kind":"execution_start","agent_id":"coordinator","input":"from-run-dir","call_id":"9"}\n',
        encoding="utf-8",
    )
    (lib / "output" / "baseline" / "item1" / "r1" / "metrics.json").write_text(
        '{"scenario":"baseline","session":{"goal_success_rate":{"value":0.6}}}\n',
        encoding="utf-8",
    )
    return lib


@pytest.fixture(autouse=True)
def _reset_ui_stream_state() -> None:
    state.reset()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from fastapi import FastAPI

    lib = _write_lab(tmp_path)
    monkeypatch.setattr("mas.lab.controller.ui_stream.app._lib_dir", lambda name: lib)
    app = FastAPI()
    mount_ui_stream(app)
    return TestClient(app)


def _ws_json(ws, timeout: float = 2.0) -> dict:
    box: queue.Queue = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            box.put(("ok", ws.receive_json()))
        except Exception as exc:
            box.put(("err", exc))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        status, value = box.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"websocket receive exceeded {timeout}s") from exc
    if status == "err":
        raise value
    return value


def _wait_replay(job_id: str, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and job_id in state._replay_tasks:
        time.sleep(0.02)


def test_artefact_catalog_lists_realtime_chat(client: TestClient) -> None:
    res = client.get("/api/artefacts")
    assert res.status_code == 200
    names = {row["name"] for row in res.json()["artefacts"]}
    assert "chat" in names
    chat = next(row for row in res.json()["artefacts"] if row["name"] == "chat")
    assert chat["stream"] == "custom.chat"
    assert chat["plot"] == "chat"
    assert "/api/artefacts/" in chat["address"]


def test_artefact_address_returns_latest_snapshot(client: TestClient, tmp_path: Path) -> None:
    lib = tmp_path / "demo-lib"
    run_dir = lib / "output" / "baseline" / "item1" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "custom.chat.jsonl").write_text(
        '{"kind":"custom.chat.snapshot","cards":[{"role":"user","text":"hi"}]}\n',
        encoding="utf-8",
    )
    res = client.get("/api/artefacts/demo-lib/e1/baseline/item1/r1/chat")
    assert res.status_code == 200
    body = res.json()
    assert body["stream"] == "custom.chat"
    assert body["artefact"] == "custom.chat"
    assert body["payload"]["cards"][0]["text"] == "hi"
    assert "complete" in body
    assert body["uri"].startswith("artifact://")
    assert "events" in body["inputs"]


def test_compare_layout_refs_run_view_per_lane() -> None:
    from mas.lab.controller.ui_stream.app import layout_roots

    doc = load_layout(layout_roots(), "compare-overlays.yaml")
    kids = doc["layout"]["children"]
    assert len(kids) == 2
    assert kids[0]["dataSource"].get("overlay") == "overlay-a"
    assert kids[1]["dataSource"].get("overlay") == "overlay-b"
    blob = str(doc)
    assert "'plugin': 'plot'" in blob
    assert "'kind': 'topology'" in blob
    assert "'kind': 'chat'" in blob


def test_stock_layouts_are_examples_only() -> None:
    from mas.lab.controller.ui_stream.app import _stock_layouts

    names = {p.name for p in _stock_layouts().glob("*.yaml")}
    assert names == set()
    examples = {p.name for p in (_stock_layouts() / "examples").glob("*.yaml")}
    assert "compare-overlays.yaml" in examples
    assert "run-view.yaml" in examples


def test_series_slug_resolves_without_a_named_catalog_row(client: TestClient) -> None:
    res = client.get("/api/artefacts/agents")
    assert res.status_code == 200
    body = res.json()
    assert body["stream"] == "series.agents"
    assert body["plot"] == "series"


def test_plugin_registry_lists_modules(client: TestClient) -> None:
    res = client.get("/api/plugins")
    assert res.status_code == 200
    rows = res.json()["plugins"]
    assert rows
    assert all("/src/plugins" not in str(row.get("module") or "") for row in rows)
    modules = {row.get("module") for row in rows if row.get("module")}
    assert "/src/plot-host.js" in modules
    assert "/src/chrome.js" in modules
    plots = client.get("/api/plugins", params={"kind": "plot", "format": "html", "realtime": True})
    assert plots.status_code == 200
    names = {row["id"] for row in plots.json()["plugins"]}
    assert "plot" in names


def test_replay_nonce_clears_then_ws_carries_chat_and_topology(client: TestClient) -> None:
    records = [
        {
            "kind": "execution_start",
            "agent_id": "coordinator",
            "input": "triage the request",
            "timestamp": 1,
        },
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_specialist",
            "call_id": "d1",
            "timestamp": 2,
        },
        {
            "kind": "llm_call_end",
            "agent_id": "specialist",
            "output": "ack",
            "timestamp": 3,
        },
    ]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"topics": ["*"], "subscribe": ["*"], "nonce": "ui-tab"})
        first = client.post(
            "/api/ingest",
            json={"replay": True, "nonce": "r1", "ref": {"scenario": "baseline"}, "records": records},
        )
        assert first.status_code == 200, first.text
        assert first.json()["nonce"] == "r1"
        got: list[dict] = []
        cards: list[dict] = []
        for _ in range(80):
            got.append(_ws_json(ws))
            if str(got[-1].get("topic") or "").startswith("custom.chat"):
                cards = list((got[-1].get("payload") or {}).get("cards") or [])
            topics = {item["topic"] for item in got}
            if (
                "view.reset" in topics
                and any(row.get("from") == "specialist" and row.get("depth") == 2 for row in cards)
                and any("topology" in name for name in topics)
            ):
                break
        topics = [item["topic"] for item in got]
        assert "view.reset" in topics
        assert cards[0]["role"] == "user"
        assert cards[0]["depth"] == 0
        assert any(row.get("from") == "specialist" and row.get("depth") == 2 for row in cards)
        assert any(row.get("from") == "coordinator" and row.get("depth") == 1 for row in cards)
        topo = next(item for item in reversed(got) if "topology" in str(item["topic"]))
        depth = {row["id"]: row["depth"] for row in topo["payload"]["nodes"]}
        assert depth["user"] == 0
        second = client.post(
            "/api/ingest",
            json={
                "replay": True,
                "nonce": "r2",
                "ref": {"scenario": "baseline"},
                "records": [
                    {
                        "kind": "execution_start",
                        "agent_id": "coordinator",
                        "input": "second replay",
                        "timestamp": 1,
                    }
                ],
            },
        )
        assert second.status_code == 200, second.text
        after: list[dict] = []
        for _ in range(24):
            after.append(_ws_json(ws))
            if after[-1]["topic"] == "view.reset":
                break
        assert any(item["topic"] == "view.reset" for item in after)
        chat2 = None
        for _ in range(24):
            item = _ws_json(ws)
            after.append(item)
            if str(item.get("topic") or "").startswith("custom.chat"):
                chat2 = item
                break
        assert chat2 is not None
        texts = [row.get("text") for row in chat2["payload"]["cards"]]
        assert "second replay" in texts
        assert "triage the request" not in texts


def test_ingest_folds_chat_and_topology(client: TestClient) -> None:
    res = client.post(
        "/api/ingest",
        json={
            "ref": {"scenario": "baseline"},
            "records": [
                {
                    "kind": "execution_start",
                    "agent_id": "entry",
                    "input": "hello there",
                    "timestamp": 1,
                },
                {
                    "kind": "llm_call_end",
                    "agent_id": "entry",
                    "output": "hi back",
                    "timestamp": 2,
                },
                {
                    "kind": "tool_call_start",
                    "agent_id": "entry",
                    "tool_name": "delegate_to_peer",
                    "timestamp": 3,
                },
            ],
        },
    )
    assert res.status_code == 200, res.text
    chat = state.bus.snapshot(["custom.chat*"], DataSourceRef(scenario="baseline"))
    topo = state.bus.snapshot(["custom.topology*"], DataSourceRef(scenario="baseline"))
    assert any("hello there" in str(item.payload) for item in chat)
    assert any("entry" in str(item.payload) for item in topo)
    got = client.get("/api/artefacts/chat?scenario=baseline")
    assert got.status_code == 200
    body = got.json()
    assert body["artefact"] == "custom.chat"
    assert body["complete"] is False
    assert "events" in body["inputs"]
    assert body["uri"].startswith("artifact://")
    end = client.post(
        "/api/ingest",
        json={"ref": {"scenario": "baseline"}, "records": [{"kind": "mas_call_end", "agent_id": "entry"}]},
    )
    assert end.status_code == 200, end.text
    sealed = state.bus.snapshot(["custom.chat.sealed"], DataSourceRef(scenario="baseline"))
    assert sealed
    assert sealed[-1].complete is True
    done = client.get("/api/artefacts/chat?scenario=baseline")
    assert done.json()["complete"] is True


def test_ingest_does_not_invent_story_from_tool_errors(client: TestClient) -> None:
    res = client.post(
        "/api/ingest",
        json={
            "ref": {"scenario": "baseline"},
            "records": [
                {
                    "kind": "tool_call_end",
                    "ok": False,
                    "error": "timeout",
                    "call_id": "c1",
                    "timestamp": 1,
                },
                {
                    "kind": "llm_call_error",
                    "error": "empty",
                    "call_id": "c2",
                    "timestamp": 2,
                },
                {
                    "kind": "execution_end",
                    "agent_id": "specialist",
                    "timestamp": 3,
                },
                {
                    "kind": "mas_call_end",
                    "agent_id": "mas",
                    "timestamp": 4,
                },
            ],
        },
    )
    assert res.status_code == 200, res.text
    story = state.bus.snapshot(["custom.timeline*"], DataSourceRef(scenario="baseline"))
    blob = str([item.payload for item in story]).lower()
    assert "compound" not in blob
    assert "activity" not in blob
    assert "execution_end" not in blob
    assert "mas_call_end" not in blob


def test_paced_ingest_publishes_spec_before_events(client: TestClient) -> None:
    res = client.post(
        "/api/ingest",
        json={
            "ref": {"scenario": "baseline"},
            "speed": 8,
            "records": [
                {
                    "kind": "execution_start",
                    "agent_id": "entry",
                    "input": "hello there",
                    "timestamp": 1,
                },
                {
                    "kind": "tool_call_start",
                    "agent_id": "entry",
                    "tool_name": "delegate_to_peer",
                    "timestamp": 2,
                },
            ],
        },
    )
    assert res.status_code == 200, res.text
    job_id = res.json().get("job_id")
    assert job_id
    _wait_replay(job_id)
    spec = state.bus.snapshot(["spec.snapshot"], DataSourceRef(scenario="baseline"))
    topo = state.bus.snapshot(["custom.topology*"], DataSourceRef(scenario="baseline"))
    assert spec or any("entry" in str(item.payload) for item in topo)
    got = client.get("/api/artefacts/topology?scenario=baseline")
    assert got.status_code == 200
    assert got.json().get("payload")


def test_envelope_ingest_replays_pipeline_without_recompute(client: TestClient) -> None:
    res = client.post(
        "/api/ingest/envelopes",
        json={
            "speed": 0,
            "lanes": [
                {
                    "tree": "pipeline",
                    "envelopes": [
                        {
                            "topic": "series.agents",
                            "source": "artifact",
                            "ts": "2026-01-01T00:00:01Z",
                            "ref": {"scenario": "baseline"},
                            "payload": {
                                "kind": "series.agents",
                                "series": {"agents": [0.1, 0.4]},
                            },
                        }
                    ],
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    _wait_replay(str(res.json()["job_id"]))
    ticks = state.bus.snapshot(["series.agents"], DataSourceRef(scenario="baseline"))
    assert ticks
    job_id = res.json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "completed"
    assert job.json().get("n_total")
    assert job.json().get("lanes")
    ui_job = client.get(f"/api/ui/jobs/{job_id}")
    assert ui_job.status_code == 200
    assert ui_job.json()["status"] == "completed"
    trace = client.get("/api/trace", params={"scenario": "baseline"})
    assert trace.status_code == 200
    body = trace.json()
    assert body["kind"] == "mas-lab.ui-trace"
    assert body["pipeline"]


def test_aligned_ingest_shares_one_clock(client: TestClient) -> None:
    res = client.post(
        "/api/ingest/aligned",
        json={
            "speed": 0,
            "lanes": [
                {
                    "ref": {"scenario": "baseline"},
                    "records": [
                        {
                            "kind": "execution_start",
                            "agent_id": "entry",
                            "input": "hello",
                            "timestamp": 10,
                        },
                        {
                            "kind": "tool_call_end",
                            "ok": False,
                            "error": "timeout",
                            "call_id": "c1",
                            "timestamp": 20,
                        },
                    ],
                },
                {
                    "ref": {"scenario": "arm-b"},
                    "records": [
                        {
                            "kind": "custom.panel-a.snapshot",
                            "outcome": "SUCCESS",
                            "x_star": "pack-1",
                            "timestamp": 15,
                        }
                    ],
                },
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("lanes") == 2
    _wait_replay(str(body["job_id"]))
    panel = state.bus.snapshot(["custom.panel-a*"], DataSourceRef(scenario="arm-b"))
    assert panel
    story = state.bus.snapshot(["custom.timeline*"], DataSourceRef(scenario="arm-b"))
    blob = str([item.payload for item in story]).lower()
    assert "compound" not in blob
    assert "activity" not in blob


def test_get_plot_defaults_to_embedded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAS_UI_PLOTS", "mas.library.lab.ui.series:SeriesPlots")
    from mas.lab.plots.providers import load_plot_providers

    load_plot_providers(refresh=True)
    res = client.get("/api/plots/chat")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "embedded"
    assert body["kind"] == "chat"
    assert body["stream"] == "custom.chat"
    assert "masDraw" in body["js"]
    assert res.headers.get("X-Mas-Plot-Stream") == "custom.chat"
    html = client.get("/api/plots/chat?format=html")
    assert html.status_code == 200
    assert "masUpdateChart" in html.text
    draw = client.get("/api/plots/chat/draw.js")
    assert draw.status_code == 200
    assert "export default function install" in draw.text
    assert "masDraw" in draw.text
    missing = client.get("/api/plots/not-a-plot")
    assert missing.status_code == 404
    loop = client.get("/api/plots/loop")
    assert loop.status_code == 200
    assert loop.headers.get("X-Mas-Plot-Stream") == "series"
    assert "masDraw" in loop.json()["js"]
    spec = client.get("/api/plots/chat?mode=embedded").json()
    assert spec["mode"] == "embedded"
    assert "function esc" in spec["js"]
    assert "nestCards" not in spec["js"]
    assert "cardDepth(c) * 20" in spec["js"]
    assert "scrollHeight" in spec["js"]
    assert ":scope > div.bubble" in spec["js"]
    assert ".bubble" in spec["css"]
    assert "font-size: 12px" in spec["css"]


def test_root_lists_demo_layouts(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in (res.headers.get("content-type") or "")
    assert "Demo layouts" in res.text
    assert ".yaml" in res.text


def test_api_layouts_returns_catalog(client: TestClient) -> None:
    res = client.get("/api/layouts")
    assert res.status_code == 200
    body = res.json()
    assert "run-view.yaml" in body["layouts"]
    catalog = {row["slug"]: row for row in body["catalog"]}
    assert catalog["run-view.yaml"]["title"]
    assert catalog["run-view.yaml"]["slug"] == "run-view.yaml"


def test_get_layout_returns_json(client: TestClient) -> None:
    res = client.get("/api/layouts/run-view.yaml")
    assert res.status_code == 200
    body = res.json()
    titles = [child.get("title") for child in body["layout"]["children"]]
    assert "Chat" in titles or any("Chat" in str(child) for child in body["layout"]["children"])


def test_get_layout_survives_hydrate_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_doc: object) -> None:
        raise RuntimeError("hydrate failed")

    monkeypatch.setattr("mas.lab.controller.ui_stream.app._hydrate_specs", boom)
    res = client.get("/api/layouts/run-view.yaml")
    assert res.status_code == 200
    assert "layout" in res.json()


def test_layout_has_no_paths() -> None:
    from mas.lab.controller.ui_stream.app import layouts_root

    doc = load_layout(layouts_root(), "run-view.yaml")
    blob = str(doc)
    assert "events_path" not in blob
    assert "Topology" in blob


def test_layout_roots_from_library_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.controller.ui_stream.app import layout_roots

    lab = tmp_path / "labs" / "demo.lab" / "demo-ui"
    (lab / "layouts").mkdir(parents=True)
    (lab / "layouts" / "lab-run.yaml").write_text("version: 1\nlayout: {}\n", encoding="utf-8")
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(tmp_path))
    roots = layout_roots()
    assert any((root / "lab-run.yaml").is_file() for root in roots)


def test_layout_roots_accepts_a_layouts_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.controller.ui_stream.app import layout_roots

    layouts = tmp_path / "pack" / "demo-ui" / "layouts"
    layouts.mkdir(parents=True)
    (layouts / "extra-run.yaml").write_text("version: 1\ntitle: Extra\nlayout: {}\n", encoding="utf-8")
    monkeypatch.delenv("MAS_DEMO_UI_LAYOUTS", raising=False)
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(layouts))
    roots = layout_roots()
    assert any((root / "extra-run.yaml").is_file() for root in roots)

    nested = tmp_path / "repo" / "library-pack" / "demo-ui" / "layouts"
    nested.mkdir(parents=True)
    (nested / "nested-run.yaml").write_text("version: 1\ntitle: Nested\nlayout: {}\n", encoding="utf-8")
    monkeypatch.setenv("MAS_LIBRARY_PATHS", str(tmp_path / "repo"))
    roots = layout_roots()
    assert any((root / "nested-run.yaml").is_file() for root in roots)


def test_resolve_run_job_uuid_finds_latest(tmp_path: Path) -> None:
    lib = _write_lab(tmp_path)
    record = resolve_run(
        lib,
        DataSourceRef(library="demo-lib", scenario="baseline", run="job-uuid-not-a-run"),
    )
    assert record.events is not None
    assert "item1" in record.events.parts
    assert record.artefacts.get("metrics") is not None
    assert record.artefacts["metrics"].name == "metrics.json"


def test_fold_run_artefact_metrics_job_id_finds_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.controller.ui_stream.app import _fold_run_artefact_metrics

    lib = _write_lab(tmp_path)
    monkeypatch.setattr("mas.lab.controller.ui_stream.app._lib_dir", lambda name: lib)
    ref = DataSourceRef(library="demo-lib", experiment="scenario-1-ab", run="worker-job-id")
    _fold_run_artefact_metrics(ref)
    topics = [item.topic for item in state.bus.snapshot(("metric.*",), DataSourceRef(scenario="baseline"))]
    assert any(topic.startswith("metric.") for topic in topics)


def test_fold_run_artefact_metrics_publishes_gold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.lab.controller.ui_stream.app import _fold_run_artefact_metrics

    lib = _write_lab(tmp_path)
    monkeypatch.setattr("mas.lab.controller.ui_stream.app._lib_dir", lambda name: lib)
    ref = DataSourceRef(library="demo-lib", experiment="scenario-1-ab", scenario="arm-b")
    _fold_run_artefact_metrics(ref)
    topics = [item.topic for item in state.bus.snapshot(("metric.*",), ref)]
    assert any(topic.startswith("metric.") for topic in topics)


def test_overlay_rel_skips_cache_versions(tmp_path: Path) -> None:
    lib = _write_lab(tmp_path)
    found = overlay_rel(lib, "baseline-round-cap")
    assert found is not None
    assert "overlays" in found.parts
    assert ".cache" not in found.parts


def test_resolve_run_prefers_standard_dir(tmp_path: Path) -> None:
    lib = _write_lab(tmp_path)
    record = resolve_run(
        lib,
        DataSourceRef(library="demo-lib", scenario="baseline", test="1", run="1"),
    )
    assert record.events is not None
    assert record.events.name == "events.jsonl"
    assert "output" in record.events.parts


def test_resolve_run_falls_back_to_gold(tmp_path: Path) -> None:
    lib = _write_lab(tmp_path)
    record = resolve_run(lib, DataSourceRef(library="demo-lib", scenario="arm-b"))
    assert record.events is not None
    assert record.events.name == "arm-b.events.jsonl"


def test_spec_snapshot_from_experiment_and_overlay(tmp_path: Path) -> None:
    lib = _write_lab(tmp_path)
    snap = snapshot_for_ref(lib, experiment="scenario-1-ab", overlay="baseline-round-cap")
    assert snap["kind"] == "mas_topology"
    assert snap["entry"] == "coordinator"
    by_id = {item["id"]: item for item in snap["agents"]}
    assert "orchestrator" in by_id["coordinator"]["skills"]
    assert "policy" in by_id["specialist"]["skills"]


def test_overlay_isolation_on_bus() -> None:
    from mas.lab.controller.ui_stream.bus import EventBus

    bus = EventBus()
    left = DataSourceRef(overlay="baseline-round-cap")
    right = DataSourceRef(overlay="with-overlay-b")
    bus.publish(envelope("native.llm_call_start", left, "native", {"kind": "llm_call_start"}))
    bus.publish(envelope("native.llm_call_start", right, "native", {"kind": "llm_call_start"}))
    assert bus.snapshot(["native.*"], left)[0].ref.overlay == "baseline-round-cap"
    assert bus.snapshot(["native.*"], right)[0].ref.overlay == "with-overlay-b"


def test_scenario_lane_ignores_unstamped_events() -> None:
    from mas.lab.controller.ui_stream.bus import EventBus

    bus = EventBus()
    left = DataSourceRef(library="demo-lib", scenario="baseline")
    right = DataSourceRef(library="demo-lib", scenario="arm-b")
    bus.publish(
        envelope("native.execution_start", DataSourceRef(library="demo-lib"), "native", {"kind": "execution_start"})
    )
    bus.publish(
        envelope(
            "native.execution_start",
            DataSourceRef(library="demo-lib", scenario="baseline"),
            "native",
            {"kind": "execution_start", "input": "baseline-only"},
        )
    )
    bus.publish(
        envelope(
            "native.execution_start",
            DataSourceRef(library="demo-lib", scenario="arm-b"),
            "native",
            {"kind": "execution_start", "input": "arm-b-only"},
        )
    )
    assert bus.snapshot(["native.*"], left)[0].payload["input"] == "baseline-only"
    assert bus.snapshot(["native.*"], right)[0].payload["input"] == "arm-b-only"


def test_replay_by_ids_no_path(client: TestClient) -> None:
    res = client.post(
        "/api/commands",
        json={
            "verb": "replay",
            "ref": {
                "library": "demo-lib",
                "experiment": "scenario-1-ab",
                "scenario": "baseline",
                "test": "1",
                "run": "1",
                "mode": "replay",
            },
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()["results"][0]
    assert row["ok"] is True
    assert "events_path" not in res.text
    job = row["job"]
    assert job["streams"]["observability"] == "native"
    assert job["ws"] == "/ws"
    _wait_replay(str(job["job_id"]))
    frames = state.bus.snapshot(
        ["native.*"],
        DataSourceRef(library="demo-lib", scenario="baseline"),
    )
    kinds = [item.topic for item in frames]
    assert "native.execution_start" in kinds
    payload = next(item.payload for item in frames if item.topic == "native.execution_start")
    assert payload["input"] == "from-run-dir"


def test_replay_gold_when_no_run_dir(client: TestClient) -> None:
    res = client.post(
        "/api/commands",
        json={
            "verb": "replay",
            "ref": {
                "library": "demo-lib",
                "experiment": "scenario-1-ab",
                "scenario": "arm-b",
                "mode": "replay",
            },
        },
    )
    assert res.status_code == 200, res.text
    job_id = str(res.json()["results"][0]["job"]["job_id"])
    _wait_replay(job_id)
    frames = state.bus.snapshot(
        ["native.*"],
        DataSourceRef(library="demo-lib", scenario="arm-b"),
    )
    inputs = [item.payload.get("input") for item in frames if item.topic == "native.execution_start"]
    assert "from-gold" in inputs
    metrics = state.bus.snapshot(
        ["metric.*"],
        DataSourceRef(library="demo-lib", scenario="arm-b"),
    )
    snaps = [item.payload for item in metrics if item.topic == "metric.snapshot"]
    assert snaps
    last = snaps[-1]
    assert last["total_tokens"] == 120
    assert last["cached_tokens"] == 40
    assert last["n_llm_calls"] == 1
    assert last["duration_s"] == 3.0
    assert last["mce.goal_success_rate"] == 0.8


def test_ws_inspect_hello_is_named_not_star(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"topics": ["*"], "inspect": [], "ref": {"session": "live-ctx"}})
        client.post(
            "/api/ingest",
            json={
                "ref": {"session": "live-ctx"},
                "record": {
                    "kind": "context_assembled",
                    "total_tokens": 77,
                    "messages": [{"role": "system", "content": "secret"}],
                },
            },
        )
        ws.close()
    assert not state.bus.snapshot(("governance.context",), DataSourceRef(session="live-ctx"))
    assert not state.bus.snapshot(("native.context_assembled",), DataSourceRef(session="live-ctx"))

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "topics": ["governance.context"],
                "inspect": ["context"],
                "ref": {"session": "live-ctx"},
            }
        )
        posted = client.post(
            "/api/ingest",
            json={
                "ref": {"session": "live-ctx"},
                "record": {
                    "kind": "context_assembled",
                    "total_tokens": 77,
                    "segments": 1,
                    "messages": [{"role": "system", "content": "secret"}],
                },
            },
        )
        assert posted.status_code == 200
        found = None
        for _ in range(8):
            item = _ws_json(ws)
            if item["topic"] == "governance.context":
                found = item
                break
        assert found is not None
        assert found["payload"]["context_tokens"] == 77
        assert "messages" not in found["payload"]


def test_ws_catchup_includes_chat_when_hello_get_is_spec(client: TestClient) -> None:
    posted = client.post(
        "/api/ingest",
        json={
            "replay": True,
            "speed": 0,
            "nonce": "r-catchup",
            "ref": {"scenario": "baseline"},
            "records": [
                {
                    "kind": "execution_start",
                    "agent_id": "coordinator",
                    "input": "triage the request",
                    "timestamp": 1,
                },
                {
                    "kind": "llm_call_end",
                    "agent_id": "specialist",
                    "output": "ack",
                    "timestamp": 2,
                },
            ],
        },
    )
    assert posted.status_code == 200, posted.text
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "get": ["spec"],
                "subscribe": ["custom.chat", "custom.topology", "series.agents"],
                "nonce": "ui-tab",
            }
        )
        found: dict[str, dict] = {}
        for _ in range(16):
            item = _ws_json(ws)
            found[str(item.get("topic") or "")] = item
            if any(name.startswith("custom.chat") for name in found):
                break
        assert any(name.startswith("custom.chat") for name in found)
        cards = list((found[next(name for name in found if name.startswith("custom.chat"))].get("payload") or {}).get("cards") or [])
        assert any("triage the request" in str(row.get("text") or "") for row in cards)


def test_ws_subscribe_and_live_ingest(client: TestClient) -> None:
    posted = client.post(
        "/api/ingest",
        json={
            "ref": {"session": "live-1", "run": "ws-1"},
            "record": {"kind": "hitl_gate", "agent_id": "coordinator", "call_id": "g1"},
        },
    )
    assert posted.status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"topics": ["native.hitl_gate", "control.hitl_gate"], "ref": {"session": "live-1", "run": "ws-1"}})
        found: set[str] = set()
        for _ in range(8):
            item = _ws_json(ws)
            found.add(item["topic"])
            if {"native.hitl_gate", "control.hitl_gate"} <= found:
                break
        assert "native.hitl_gate" in found
        assert "control.hitl_gate" in found


def test_ingest_takes_scenario_from_record(client: TestClient) -> None:
    posted = client.post(
        "/api/ingest",
        json={
            "ref": {"library": "demo-lib", "experiment": "scenario-1-ab", "run": "job-1"},
            "record": {
                "kind": "llm_call_end",
                "scenario": "baseline",
                "usage": {"total_tokens": 12},
            },
        },
    )
    assert posted.status_code == 200
    snap = state.metrics.snapshot(
        DataSourceRef(library="demo-lib", experiment="scenario-1-ab", scenario="baseline", run="job-1")
    )
    other = state.metrics.snapshot(
        DataSourceRef(library="demo-lib", experiment="scenario-1-ab", scenario="arm-b", run="job-1")
    )
    assert int(snap.get("total_tokens") or 0) >= 12
    assert int(other.get("total_tokens") or 0) == 0


def test_live_infra_uses_workspace_and_scenario_cache(tmp_path: Path) -> None:
    from mas.lab.controller.ui_stream.app import _live_infra
    from mas.lab.controller.ui_stream.refs import DataSourceRef

    lib = tmp_path / "demo-lib"
    (lib / "infra").mkdir(parents=True)
    (lib / "config.yaml").write_text("infra_refs:\n  - ./infra/litellm-haiku.yaml\n", encoding="utf-8")
    (lib / "infra" / "litellm-haiku.yaml").write_text("kind: LLMProxy\n", encoding="utf-8")
    (lib / "infra" / "llm-cache-demo-baseline-replay.yaml").write_text("kind: InfraMiddleware\n", encoding="utf-8")
    (lib / "infra" / "llm-cache-demo-arm-b-replay.yaml").write_text("kind: InfraMiddleware\n", encoding="utf-8")
    base, per = _live_infra(
        lib,
        [
            DataSourceRef(scenario="baseline"),
            DataSourceRef(scenario="arm-b"),
        ],
    )
    assert base[0].endswith("litellm-haiku.yaml")
    assert per["baseline"][-1].endswith("llm-cache-demo-baseline-replay.yaml")
    assert per["arm-b"][-1].endswith("llm-cache-demo-arm-b-replay.yaml")
    assert per["baseline"][0] == base[0]


def test_live_overlays_from_layout_targets() -> None:
    from mas.lab.controller.ui_stream.app import _live_overlays
    from mas.lab.controller.ui_stream.refs import DataSourceRef

    per = _live_overlays(
        [
            DataSourceRef(scenario="baseline", overlay="baseline-round-cap"),
            DataSourceRef(scenario="arm-b", overlay="with-overlay-b"),
            DataSourceRef(scenario="baseline", overlay="baseline-round-cap"),
        ]
    )
    assert per == {
        "baseline": ["baseline-round-cap"],
        "arm-b": ["with-overlay-b"],
    }


def test_live_experiment_returns_streams(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeApi:
        def submit_experiment(self, spec):
            assert spec.get("demo_ui_ingest_url", "").endswith("/api/ingest")
            ref = spec.get("demo_ui_ref") or {}
            assert "events_path" not in ref
            assert ref.get("experiment") == "scenario-1-ab"
            assert spec.get("cwd")
            assert "infra_refs" in spec
            return {"job_id": "exp-1", "status": "running"}

    monkeypatch.setattr("mas.lab.controller.api.get_controller_api", lambda: FakeApi())
    monkeypatch.setattr(
        "mas.lab.controller.routes.experiments.resolve_experiment_path",
        lambda lib_dir, experiment: Path(lib_dir) / "experiment.yaml",
    )
    monkeypatch.setattr("mas.lab.controller.routes.run._run_pre_steps", lambda *a, **k: None)
    res = client.post(
        "/api/commands",
        json={
            "verb": "run-experiment",
            "ref": {
                "library": "demo-lib",
                "experiment": "scenario-1-ab",
                "mode": "live",
            },
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()["results"][0]
    assert row["ok"] is True
    assert row["job"]["id"] == "exp-1"
    assert row["streams"]["hitl"] == "control"
    runs = client.get("/api/runs").json()["runs"]
    assert any(job.get("id") == "exp-1" for group in runs for job in group.get("jobs") or [])


def test_live_experiment_forwards_lane_overlays(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class FakeApi:
        def submit_experiment(self, spec):
            seen.update(spec)
            return {"job_id": "exp-overlays", "status": "running"}

    monkeypatch.setattr("mas.lab.controller.api.get_controller_api", lambda: FakeApi())
    monkeypatch.setattr(
        "mas.lab.controller.routes.experiments.resolve_experiment_path",
        lambda lib_dir, experiment: Path(lib_dir) / "experiment.yaml",
    )
    monkeypatch.setattr("mas.lab.controller.routes.run._run_pre_steps", lambda *a, **k: None)
    res = client.post(
        "/api/commands",
        json={
            "verb": "run-experiment",
            "ref": {"library": "demo-lib", "experiment": "scenario-1-ab", "mode": "live"},
            "targets": [
                {"scenario": "baseline", "overlay": "baseline-round-cap"},
                {"scenario": "arm-b", "overlay": "with-overlay-b"},
            ],
        },
    )
    assert res.status_code == 200, res.text
    assert seen["scenario_overlays"]["baseline"] == ["baseline-round-cap"]
    assert seen["scenario_overlays"]["arm-b"] == ["with-overlay-b"]


def test_run_mas_listed_by_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_mas(library, req):
        assert req.mas_path == "mas/scenario-1.mas.yaml"
        assert "events_path" not in (req.demo_ui_ref or {})
        return {"job_id": "mas-1", "status": "running", "session_id": "sess-9"}

    monkeypatch.setattr("mas.lab.controller.routes.run.run_mas", fake_run_mas)
    res = client.post(
        "/api/commands",
        json={
            "verb": "run-mas",
            "query": "hello",
            "ref": {
                "library": "demo-lib",
                "experiment": "scenario-1-ab",
                "mode": "live",
            },
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()["results"][0]
    assert row["job"]["session"] == "sess-9"
    runs = client.get("/api/runs").json()["runs"]
    jobs = [job for group in runs for job in group.get("jobs") or []]
    assert any(job.get("session") == "sess-9" and job.get("id") == "mas-1" for job in jobs)


def test_reset_view_clears_history_and_drops_followup_ingest(client: TestClient) -> None:
    posted = client.post(
        "/api/ingest",
        json={
            "ref": {"library": "sample-demo", "scenario": "baseline", "run": "job-old"},
            "record": {"kind": "execution_start", "agent_id": "lead", "input": "before-reset"},
        },
    )
    assert posted.status_code == 200
    before = state.bus.snapshot(
        ["native.*"],
        DataSourceRef(library="sample-demo", scenario="baseline"),
    )
    assert any(item.payload.get("input") == "before-reset" for item in before)
    state._runs.append({"id": "job-old", "origin": "demo-ui", "library": "sample-demo"})
    state._job_refs["job-old"] = DataSourceRef(run="job-old", origin="demo-ui")
    res = client.post("/api/commands", json={"verb": "reset-view", "ref": {"origin": "demo-ui"}})
    assert res.status_code == 200, res.text
    kinds = {item.topic for item in state.bus.snapshot(("*",), DataSourceRef())}
    assert "native.execution_start" not in kinds
    assert client.get("/api/runs").json()["runs"] == []
    dropped = client.post(
        "/api/ingest",
        json={
            "ref": {"library": "sample-demo", "scenario": "baseline", "run": "job-old"},
            "record": {"kind": "execution_start", "agent_id": "lead", "input": "after-reset"},
        },
    )
    assert dropped.json().get("dropped") is True
    after = state.bus.snapshot(
        ["native.*"],
        DataSourceRef(library="sample-demo", scenario="baseline"),
    )
    assert after == []
    replayed = client.post(
        "/api/ingest",
        json={
            "ref": {"library": "sample-demo", "scenario": "baseline", "run": "job-new"},
            "record": {"kind": "execution_start", "agent_id": "lead", "input": "fresh-replay"},
        },
    )
    assert replayed.status_code == 200
    assert replayed.json().get("dropped") is not True
    fresh = state.bus.snapshot(
        ["native.*"],
        DataSourceRef(library="sample-demo", scenario="baseline"),
    )
    assert any(item.payload.get("input") == "fresh-replay" for item in fresh)


def test_replay_flag_opens_ingest_when_view_is_closed(client: TestClient) -> None:
    state._closed_until_run = True
    res = client.post(
        "/api/ingest",
        json={
            "replay": True,
            "speed": 8,
            "ref": {"scenario": "baseline"},
            "records": [
                {"kind": "execution_start", "agent_id": "lead", "timestamp": 1},
                {"kind": "execution_end", "timestamp": 2},
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("dropped") is not True
    assert body.get("job_id")


def test_run_experiment_reopens_ingest_after_reset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/api/commands", json={"verb": "reset-view", "ref": {"origin": "demo-ui"}})

    class FakeApi:
        def submit_experiment(self, spec):
            return {"job_id": "exp-2", "status": "running"}

    monkeypatch.setattr("mas.lab.controller.api.get_controller_api", lambda: FakeApi())
    monkeypatch.setattr(
        "mas.lab.controller.routes.experiments.resolve_experiment_path",
        lambda lib_dir, experiment: Path(lib_dir) / "experiment.yaml",
    )
    monkeypatch.setattr("mas.lab.controller.routes.run._run_pre_steps", lambda *a, **k: None)
    started = client.post(
        "/api/commands",
        json={
            "verb": "run-experiment",
            "ref": {
                "library": "demo-lib",
                "experiment": "scenario-1-ab",
                "mode": "live",
            },
        },
    )
    assert started.status_code == 200, started.text
    posted = client.post(
        "/api/ingest",
        json={
            "ref": {"library": "demo-lib", "scenario": "baseline"},
            "record": {"kind": "execution_start", "agent_id": "coordinator", "input": "live-again"},
        },
    )
    assert posted.json().get("dropped") is not True
    frames = state.bus.snapshot(
        ["native.*"],
        DataSourceRef(library="demo-lib", scenario="baseline"),
    )
    assert any(item.payload.get("input") == "live-again" for item in frames)


def test_ingest_control_structure_and_artefact_ticks() -> None:
    from mas.lab.controller.ui_stream.ingest import NativeIngest
    from mas.lab.controller.ui_stream.metrics import MetricsStore

    bus_topics: list[str] = []

    class _Bus:
        def publish(self, env) -> None:
            bus_topics.append(env.topic)

        def demands_topic(self, topic, ref) -> bool:
            return False

    ingest = NativeIngest(_Bus(), MetricsStore())
    ref = DataSourceRef(library="demo-lib", scenario="arm-b", test="item1", run="r1")
    ingest.ingest(ref, {"kind": "control.structure", "offset": 4, "kinds": {"llm_call_end": 2}, "digest": "abc"})
    ingest.ingest(ref, {"kind": "metric", "name": "trace_stats", "metrics": {"n_llm_calls": 2}})
    ingest.ingest(ref, {"kind": "scope.progress", "done": 1, "total": 2, "scope": "item"})
    assert "control.structure" in bus_topics
    assert "metric.snapshot" in bus_topics
    assert "artifact.metric" in bus_topics
    assert "metric.scope" in bus_topics
    assert "job.progress" in bus_topics
