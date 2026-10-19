#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

from mas.lab.controller.ui_stream.plot_folds import fold_chat_event, fold_topology_event, topology_from_spec
from mas.lab.controller.ui_stream.replay_cli import (
    align_sidecar,
    discover_traces,
    load_records,
    describe_target,
    format_replay_progress_line,
    live_fold_lanes,
    post_ingest,
    replay_speed,
    records_from_sidecar,
    replay_target,
)
from mas.lab.controller.ui_stream.spec_snapshot import merge_spec_snapshots, snapshot_from_records
from mas.lab.controller.ui_stream.timeline import merge_timeline, pump_timeline, records_to_scheduled
from mas.lab.controller.ui_stream.artefact_bind import (
    canonical_artefact,
    graph_from_steps,
    name_of_stream,
    stream_of,
)
from mas.lab.lab.config.artifact_types import artefact_uri, parse_artefact_uri
from mas.lab.controller.ui_stream.ui_trace import (
    artefact_filename,
    artefact_key,
    cached_pipeline_files,
    discover_ui_traces,
    envelopes_digest,
    is_ui_trace_dir,
    read_ui_trace,
    tree_of,
    write_ui_trace,
)


def test_discover_traces_file_and_lab(tmp_path: Path) -> None:
    lab = tmp_path / "demo.lab"
    cache = lab / "infra" / ".cache"
    cache.mkdir(parents=True)
    a = cache / "baseline.events.jsonl"
    b = cache / "arm-b.events.jsonl"
    a.write_text('{"kind":"execution_start","agent_id":"entry"}\n', encoding="utf-8")
    b.write_text('{"kind":"execution_start","agent_id":"entry"}\n', encoding="utf-8")
    found = discover_traces(lab)
    names = {name for name, _ in found}
    assert names == {"baseline", "arm-b"}
    one = discover_traces(a)
    assert one == [("baseline", a.resolve())]


def test_ui_trace_dir_wins_over_nested_events(tmp_path: Path) -> None:
    lab = tmp_path / "lab"
    (lab / "traces").mkdir(parents=True)
    (lab / "traces" / "events.jsonl").write_text(
        '{"kind":"execution_start","timestamp":1}\n',
        encoding="utf-8",
    )
    dest = write_ui_trace(
        lab / "ui-trace",
        [{"topic": "native.llm_call_end", "payload": {"kind": "llm_call_end"}, "ts": "1"}],
    )
    hits = discover_ui_traces(lab)
    assert [name for name, _ in hits] == ["lab"]
    assert hits[0][1].resolve() == dest.resolve()
    assert discover_traces(lab)
    assert discover_traces(lab / "traces" / "events.jsonl")[0][1].name == "events.jsonl"


def test_load_records_skips_blank(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"kind":"a"}\n\n{"kind":"b"}\n', encoding="utf-8")
    rows = load_records(path)
    assert [row["kind"] for row in rows] == ["a", "b"]


def test_fold_chat_event_keeps_user_and_depth() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {
            "kind": "execution_start",
            "agent_id": "coordinator",
            "input": "start the task",
            "timestamp": 100.0,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "d1",
            "timestamp": 101.0,
        },
    )
    fold_chat_event(
        state,
        {"kind": "llm_call_end", "agent_id": "alpha", "output": "recommend option A", "timestamp": 112.0},
    )
    fold_chat_event(
        state,
        {
            "kind": "execution_start",
            "agent_id": "alpha",
            "input": "Task brief for alpha.",
            "timestamp": 111.0,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "user_response",
            "agent_id": "alpha",
            "content": "**Alpha note:** recommend option A",
            "timestamp": 112.0,
        },
    )
    cards = state["cards"]
    assert state["t0"] == 100.0
    assert cards[0]["role"] == "user"
    assert cards[0]["from"] == "user"
    assert cards[0]["depth"] == 0
    assert cards[0]["text"] == "start the task"
    assert cards[0]["t"] == 100.0
    coordinator = next(row for row in cards if row.get("from") == "coordinator")
    assert coordinator["role"] == "reply"
    assert coordinator["depth"] == 1
    tools = coordinator.get("tools") or []
    assert tools and tools[0]["tool"] == "delegate_to_alpha"
    assert tools[0]["t"] == 101.0
    alpha = next(row for row in cards if row.get("from") == "alpha")
    assert alpha["role"] == "reply"
    assert alpha["depth"] == 2
    assert alpha["t"] == 112.0
    assert "Alpha note" in alpha["text"]
    assert all(row.get("role") != "user" or row is cards[0] for row in cards)
    assert all(row.get("from") != "user" or row is cards[0] for row in cards)


def test_fold_chat_event_starts_a_new_turn_after_the_reply() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "r1",
            "timestamp": 39.0,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "llm_call_end",
            "agent_id": "coordinator",
            "output": "round one synthesis",
            "timestamp": 40.0,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "r2",
            "timestamp": 89.4,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "llm_call_end",
            "agent_id": "coordinator",
            "output": "round two synthesis",
            "timestamp": 90.0,
        },
    )
    mods = [row for row in state["cards"] if row.get("from") == "coordinator"]
    assert len(mods) == 2
    assert mods[0]["text"] == "round one synthesis"
    assert [tool["call_id"] for tool in mods[0]["tools"]] == ["r1"]
    assert mods[1]["text"] == "round two synthesis"
    assert [tool["call_id"] for tool in mods[1]["tools"]] == ["r2"]


def test_fold_chat_splits_delegate_waves_by_time() -> None:
    state: dict = {}
    waves = (39.0, 89.4, 131.0, 161.7)
    names = ("alpha", "beta", "gamma", "delta")
    for round_n, t in enumerate(waves, start=1):
        for name in names:
            fold_chat_event(
                state,
                {
                    "kind": "tool_call_start",
                    "agent_id": "coordinator",
                    "tool_name": f"delegate_to_{name}",
                    "call_id": f"{name}-{round_n}",
                    "timestamp": t,
                },
            )
    mods = [row for row in state["cards"] if row.get("from") == "coordinator"]
    assert len(mods) == 4
    for card, t in zip(mods, waves):
        assert len(card.get("tools") or []) == 4
        assert all(abs(float(tool["t_start"]) - t) < 0.01 for tool in card["tools"])


def test_fold_chat_splits_on_parallel_group() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {"kind": "parallel_group_start", "agent_id": "coordinator", "timestamp": 10.0},
    )
    fold_chat_event(
        state,
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "a1",
            "timestamp": 10.0,
        },
    )
    fold_chat_event(
        state,
        {"kind": "parallel_group_start", "agent_id": "coordinator", "timestamp": 10.4},
    )
    fold_chat_event(
        state,
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "a2",
            "timestamp": 10.4,
        },
    )
    mods = [row for row in state["cards"] if row.get("from") == "coordinator"]
    assert len(mods) == 2
    assert [tool["call_id"] for tool in mods[0]["tools"]] == ["a1"]
    assert [tool["call_id"] for tool in mods[1]["tools"]] == ["a2"]


def test_fold_chat_nests_replies_after_delegate_ends() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "d1",
            "timestamp": 10.0,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "tool_call_end",
            "agent_id": "coordinator",
            "tool_name": "delegate_to_alpha",
            "call_id": "d1",
            "output": "recommend option B",
            "timestamp": 12.0,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "user_response",
            "agent_id": "alpha",
            "content": "<|channel>thought\n<channel|>recommend option B",
            "timestamp": 12.1,
        },
    )
    alpha = next(row for row in state["cards"] if row.get("from") == "alpha")
    assert alpha["depth"] == 2
    assert alpha["text"] == "recommend option B"
    coordinator = next(row for row in state["cards"] if row.get("from") == "coordinator")
    assert coordinator["depth"] == 1


def test_fold_chat_event_keeps_rounds_when_others_speak() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {"kind": "execution_start", "agent_id": "entry", "input": "start the task", "timestamp": 1.0},
    )
    speakers = ("alpha", "beta")
    for round_n in (1, 2, 3):
        base = float(round_n * 10)
        for name in speakers:
            fold_chat_event(
                state,
                {
                    "kind": "tool_call_start",
                    "agent_id": "entry",
                    "tool_name": f"delegate_to_{name}",
                    "call_id": f"{name}-{round_n}",
                    "timestamp": base,
                },
            )
        for offset, name in enumerate(speakers, start=1):
            fold_chat_event(
                state,
                {
                    "kind": "user_response",
                    "agent_id": name,
                    "content": f"{name} round {round_n}",
                    "timestamp": base + offset,
                },
            )
        fold_chat_event(
            state,
            {
                "kind": "llm_call_end",
                "agent_id": "entry",
                "output": f"round {round_n} synthesis",
                "timestamp": base + 5,
            },
        )
    fold_chat_event(
        state,
        {
            "kind": "user_response",
            "agent_id": "entry",
            "content": "final answer to the user",
            "timestamp": 40.0,
        },
    )
    froms = [row.get("from") for row in state["cards"]]
    assert froms[0] == "user"
    assert froms[1:4] == ["entry", "alpha", "beta"]
    assert froms.count("entry") >= 4
    assert froms.count("alpha") == 3
    assert froms.count("beta") == 3
    assert froms[-1] == "entry"
    assert state["cards"][-1]["text"] == "final answer to the user"
    assert "round 1 synthesis" in {row.get("text") for row in state["cards"]}
    assert "round 3 synthesis" in {row.get("text") for row in state["cards"]}


def test_fold_chat_event_skips_briefing_and_llm_role_user() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {"kind": "mas_call_start", "agent_id": "mas", "timestamp": 0},
    )
    fold_chat_event(
        state,
        {
            "kind": "execution_start",
            "agent_id": "specialist",
            "input": "## Specialist briefing\n**Peer assessment follows.**",
            "timestamp": 1791556896.5,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "llm_call_end",
            "agent_id": "specialist",
            "output": "Peer assessment is ready",
            "role": "user",
            "timestamp": 1791556901.7,
        },
    )
    fold_chat_event(
        state,
        {
            "kind": "user_response",
            "agent_id": "specialist",
            "content": "Peer assessment is ready",
            "role": "user",
            "timestamp": 1791556901.7,
        },
    )
    cards = state["cards"]
    assert state["t0"] == 1791556896.5
    assert not any(row.get("role") == "user" for row in cards)
    replies = [row for row in cards if row.get("from") == "specialist"]
    assert len(replies) == 1
    assert replies[0]["role"] == "reply"
    assert "Peer assessment" in replies[0]["text"]


def test_fold_topology_event_does_not_invent_nodes() -> None:
    empty = {"kind": "topology", "nodes": [], "edges": [], "activity": []}
    out = fold_topology_event(dict(empty), {"kind": "execution_start", "agent_id": "probe"})
    assert out.get("nodes") == []
    seeded = topology_from_spec(
        {
            "entry": "coordinator",
            "agents": [{"id": "coordinator"}, {"id": "alpha"}],
            "delegates_to": {"coordinator": ["alpha"]},
        }
    )
    out = fold_topology_event(seeded, {"kind": "execution_start", "agent_id": "probe"})
    ids = {node["id"] for node in out["nodes"]}
    assert "probe" not in ids
    assert "coordinator" in ids
    assert "alpha" in ids
    out = fold_topology_event(
        seeded,
        {"kind": "user_response", "agent_id": "alpha", "timestamp": 112.0, "content": "hold"},
    )
    last = out["activity"][-1]
    assert last["from"] == "alpha"
    assert last["to"] == "coordinator"
    assert last["t"] == 112.0
    assert last["kind"] == "user_response"
    assert out["t0"] == 112.0
    pulsed = fold_topology_event(
        dict(seeded),
        {"kind": "llm_call_start", "agent_id": "alpha", "timestamp": 110.0},
    )
    running = pulsed["activity"][-1]
    assert running["to"] == "alpha"
    assert running["state"] == "running"
    assert running["kind"] == "llm_call_start"
    assert pulsed.get("idle") is False
    ended = fold_topology_event(pulsed, {"kind": "mas_call_end", "timestamp": 200.0})
    assert ended["idle"] is True
    assert all(row.get("state") == "done" for row in ended["activity"])


def test_snapshot_from_records_entry_is_delegate_root_not_mas() -> None:
    snap = snapshot_from_records(
        [
            {"kind": "mas_call_start", "agent_id": "mas", "timestamp": 0},
            {"kind": "execution_start", "agent_id": "coordinator", "timestamp": 1},
            {"kind": "tool_call_start", "agent_id": "coordinator", "tool_name": "delegate_to_alpha"},
            {
                "kind": "tool_call_start",
                "agent_id": "coordinator",
                "tool_name": "delegate_to_beta",
            },
        ]
    )
    ids = {agent["id"] for agent in snap["agents"]}
    assert snap["entry"] == "coordinator"
    assert "mas" not in ids
    assert "probe" not in ids
    assert snap["delegates_to"]["coordinator"] == ["alpha", "beta"]


def test_merge_spec_does_not_keep_stale_entry() -> None:
    leftover = {"entry": "probe", "agents": [{"id": "probe"}], "delegates_to": {}}
    inferred = snapshot_from_records(
        [
            {"kind": "execution_start", "agent_id": "coordinator"},
            {"kind": "tool_call_start", "agent_id": "coordinator", "tool_name": "delegate_to_alpha"},
        ]
    )
    snap = merge_spec_snapshots(leftover, inferred)
    assert snap["entry"] == "coordinator"


def test_snapshot_from_records_stars_from_coordinator_without_delegates() -> None:
    records = [
        {"kind": "user_response", "agent_id": "user", "content": "hello", "timestamp": 1},
        {"kind": "user_response", "agent_id": "peer_a", "content": "status", "timestamp": 6},
        {"kind": "user_response", "agent_id": "peer_b", "content": "note", "timestamp": 7},
        {"kind": "user_response", "agent_id": "peer_c", "content": "ack", "timestamp": 8},
        {"kind": "llm_call_end", "agent_id": "coordinator", "content": "Activating listed-skill.", "timestamp": 10},
        {
            "kind": "tool_call_start",
            "agent_id": "coordinator",
            "tool_name": "activate_skill",
            "timestamp": 11,
        },
    ]
    snap = snapshot_from_records(records)
    assert snap["entry"] == "coordinator"
    assert snap["delegates_to"]["coordinator"] == ["peer_a", "peer_b", "peer_c"]
    topo = topology_from_spec(snap)
    edges = {(row["from"], row["to"]) for row in topo["edges"]}
    assert ("user", "coordinator") in edges
    assert ("coordinator", "peer_a") in edges
    assert ("coordinator", "peer_b") in edges
    assert ("coordinator", "peer_c") in edges


def test_fold_chat_indents_specialists_without_delegate_tools() -> None:
    state: dict = {}
    fold_chat_event(
        state,
        {"kind": "user_response", "agent_id": "user", "content": "hello", "timestamp": 1},
    )
    fold_chat_event(
        state,
        {"kind": "user_response", "agent_id": "peer_a", "content": "status", "timestamp": 6},
    )
    fold_chat_event(
        state,
        {
            "kind": "llm_call_end",
            "agent_id": "coordinator",
            "content": "Activating listed-skill.",
            "timestamp": 10,
        },
    )
    cards = state["cards"]
    assert cards[0]["depth"] == 0
    peer = next(row for row in cards if row.get("from") == "peer_a")
    coord = next(row for row in cards if row.get("from") == "coordinator")
    assert peer["depth"] == 2
    assert coord["depth"] == 1


def test_topology_from_spec_and_records() -> None:
    records = [
        {"kind": "execution_start", "agent_id": "entry"},
        {"kind": "tool_call_start", "agent_id": "entry", "tool_name": "delegate_to_peer"},
    ]
    snap = snapshot_from_records(records)
    topo = topology_from_spec(snap)
    ids = {n["id"] for n in topo["nodes"]}
    assert "user" in ids
    assert "entry" in ids
    assert any(e["to"] == "peer" for e in topo["edges"])
    depth = {n["id"]: n["depth"] for n in topo["nodes"]}
    assert depth["user"] == 0
    assert depth["entry"] == 1
    assert depth["peer"] == 2


def test_merge_spec_keeps_delegate_peers() -> None:
    sparse = {"entry": "entry", "agents": [{"id": "entry"}], "delegates_to": {}}
    inferred = snapshot_from_records(
        [
            {"kind": "execution_start", "agent_id": "entry"},
            {"kind": "tool_call_start", "agent_id": "entry", "tool_name": "delegate_to_peer"},
        ]
    )
    snap = merge_spec_snapshots(sparse, inferred)
    ids = {a["id"] for a in snap["agents"]}
    assert "peer" in ids
    assert snap["delegates_to"]["entry"] == ["peer"]


def test_records_from_sidecar_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "sidecar.json"
    path.write_text(
        '{"outcome":"SUCCESS","t":0,"log":[{"t":0,"x_star":"a","accepted":true}]}',
        encoding="utf-8",
    )
    rows = records_from_sidecar(path)
    assert rows and rows[0]["kind"] == "custom.snapshot"
    assert rows[0]["outcome"] == "SUCCESS"
    peer = [{"timestamp": 1000.0}, {"timestamp": 1100.0}]
    aligned = align_sidecar(rows, peer)
    assert 1000.0 <= aligned[0]["timestamp"] <= 1100.0


def test_ui_trace_roundtrip_splits_runtime_and_pipeline(tmp_path: Path) -> None:
    dest = write_ui_trace(
        tmp_path / "ui-trace",
        [
            {"topic": "native.llm_call_end", "payload": {"kind": "llm_call_end"}, "ts": "1"},
            {
                "topic": "series",
                "payload": {"series": {"a": [0.2]}},
                "ts": "2",
                "complete": True,
            },
            {
                "topic": "custom.timeline.snapshot",
                "payload": {"kind": "timeline"},
                "ts": "3",
                "complete": True,
            },
        ],
    )
    assert is_ui_trace_dir(dest)
    buckets = read_ui_trace(dest)
    assert len(buckets["runtime"]) == 1
    assert len(buckets["pipeline"]) == 2
    assert buckets["runtime"][0]["artefact"] == "events"
    assert buckets["runtime"][0]["path"] == "runtime.jsonl"
    assert buckets["pipeline"][0]["path"].startswith("pipeline/")
    assert (dest / artefact_filename("series")).is_file()
    assert (dest / artefact_filename("custom.timeline")).is_file()
    digest = envelopes_digest(buckets["runtime"])
    cached = cached_pipeline_files(dest, digest)
    assert {row["artefact"] for row in cached} == {"series", "custom.timeline"}
    assert all(row["complete"] is True for row in cached)
    by_name = {row["artefact"]: row for row in cached}
    assert by_name["series"]["inputs"] == ["events"]
    assert "events" in by_name["custom.timeline"]["inputs"]
    assert all(row["uri"].startswith("artifact://") for row in cached)
    assert all(row["key"] == artefact_key(row["artefact"], digest) for row in cached)
    assert cached_pipeline_files(dest, "stale") == []
    manifest = (dest / "manifest.json").read_text(encoding="utf-8")
    assert "custom.timeline" in manifest
    assert '"graph"' in manifest
    assert tree_of("native.tool_call_start") == "runtime"
    assert tree_of("custom.chat.snapshot") == "pipeline"


def test_replay_target_recompute_stays_on_ui_trace(tmp_path: Path, monkeypatch) -> None:
    dest = write_ui_trace(
        tmp_path / "ui-trace",
        [
            {"topic": "native.execution_start", "payload": {"kind": "execution_start"}, "ts": "1"},
            {
                "topic": "series",
                "payload": {"series": {"a": [0.2]}},
                "ts": "2",
                "complete": True,
            },
        ],
    )
    posted: list[dict] = []

    def fake_post(url: str, payload: dict, **_kwargs):
        posted.append({"url": url, **payload})
        return {"ok": True, "job_id": "job-1", "n": 1}

    monkeypatch.setattr("mas.lab.controller.ui_stream.replay_cli.post_json", fake_post)
    monkeypatch.setattr(
        "mas.lab.controller.ui_stream.replay_cli.wait_job",
        lambda *_args, **_kwargs: {"id": "job-1", "status": "completed"},
    )
    results = replay_target(dest, recompute=True, wait=True)
    assert results
    assert posted[0]["recompute"] is True
    trees = {lane["tree"] for lane in posted[0]["lanes"]}
    assert "runtime" in trees
    assert "pipeline" in trees
    assert "series" in posted[0]["cached_artefacts"]


def test_incomplete_pipeline_shard_is_not_cached(tmp_path: Path) -> None:
    dest = write_ui_trace(
        tmp_path / "ui-trace",
        [
            {"topic": "native.llm_call_end", "payload": {"kind": "llm_call_end"}, "ts": "1"},
            {"topic": "custom.chat.snapshot", "payload": {"kind": "chat", "cards": []}, "ts": "2"},
        ],
    )
    digest = envelopes_digest(read_ui_trace(dest)["runtime"])
    assert cached_pipeline_files(dest, digest) == []


def test_artefact_uri_and_stream_roundtrip() -> None:
    uri = artefact_uri("custom.chat", {"experiment": "e1", "scenario": "baseline", "test": "item1", "run": "r1"})
    parsed = parse_artefact_uri(uri)
    assert parsed["name"] == "custom.chat"
    assert parsed["experiment"] == "e1"
    assert parsed["scenario"] == "baseline"
    assert parsed["test"] == "item1"
    assert parsed["run"] == "r1"
    assert canonical_artefact("chat") == "custom.chat"
    assert canonical_artefact("custom.chat.snapshot") == "custom.chat"
    assert name_of_stream("custom.chat.sealed") == "custom.chat"
    assert stream_of("custom.chat") == "custom.chat"
    assert name_of_stream("native.llm_call_end") == "events"
    assert name_of_stream("series.snapshot") == "series"
    assert name_of_stream("series.semantic-loop") == "series.semantic-loop"
    assert name_of_stream("series.mas-semantic-loop") == "series.mas-semantic-loop"
    assert name_of_stream("series.agents") == "series.agents"


def test_graph_from_steps_is_bidirectional() -> None:
    graph = graph_from_steps([{"name": "stats", "in": "trace", "out": "metrics"}])
    assert "stats" in graph["metric"]["producers"]
    assert "stats" in graph["events"]["consumers"]
    assert "events" in graph["metric"]["inputs"]
    assert graph["metric"]["outputs"] == ["metric"]
    assert "fold.chat" in graph["custom.chat"]["producers"]
    assert "events" in graph["custom.chat"]["inputs"]
    assert "fold.chat" in graph["events"]["consumers"]


def test_records_to_scheduled_skips_epoch_zero_when_unix_times_exist() -> None:
    rows = records_to_scheduled(
        [
            {"kind": "a", "timestamp": 0},
            {"kind": "b", "timestamp": 1791556896.5},
            {"kind": "c", "timestamp": 1791556897.0},
        ]
    )
    assert rows[0].ts == 1791556896.5
    assert rows[1].ts == 1791556896.5
    assert rows[2].ts == 1791556897.0


def test_pump_timeline_rebases_clock_jumps() -> None:
    import asyncio
    import time

    items = records_to_scheduled(
        [
            {"kind": "a", "timestamp": 0},
            {"kind": "b", "timestamp": 1791556896.5},
            {"kind": "c", "timestamp": 1791556897.0},
        ]
    )
    n = {"n": 0}

    def emit(_item) -> None:
        n["n"] += 1

    async def run() -> float:
        t0 = time.perf_counter()
        await pump_timeline(items, speed=1.0, emit=emit)
        return time.perf_counter() - t0

    elapsed = asyncio.run(run())
    assert n["n"] == 3
    assert elapsed < 1.5


def test_pump_timeline_paces_a_real_gap() -> None:
    import asyncio
    import time

    items = records_to_scheduled(
        [
            {"kind": "a", "timestamp": 10.0},
            {"kind": "b", "timestamp": 10.25},
        ]
    )
    n = {"n": 0}

    def emit(_item) -> None:
        n["n"] += 1

    async def run() -> float:
        t0 = time.perf_counter()
        await pump_timeline(items, speed=1.0, emit=emit)
        return time.perf_counter() - t0

    elapsed = asyncio.run(run())
    assert n["n"] == 2
    assert elapsed >= 0.2
    assert elapsed < 1.0


def test_timeline_merges_lanes_by_timestamp() -> None:
    a = records_to_scheduled([{"kind": "a", "timestamp": 30}, {"kind": "b", "timestamp": 50}], tree="runtime")
    b = records_to_scheduled([{"kind": "c", "timestamp": 40}], tree="pipeline")
    merged = merge_timeline([a, b])
    assert [row.record["kind"] for row in merged] == ["a", "c", "b"]


def test_post_ingest_marks_replay(monkeypatch) -> None:
    seen: dict = {}

    def fake_post(url, payload, timeout=120.0):
        seen.update(payload)
        return {"ok": True, "n": len(payload.get("records") or []), "job_id": "j1"}

    monkeypatch.setattr("mas.lab.controller.ui_stream.replay_cli.post_json", fake_post)
    posted = post_ingest(
        "http://127.0.0.1:8090/api/ingest",
        [{"kind": "execution_start"}],
        ref={"scenario": "baseline"},
        speed=10,
    )
    assert seen["replay"] is True
    assert seen["speed"] == 10
    assert seen["nonce"]
    assert posted["job_id"] == "j1"


def test_replay_speed_from_duration_or_explicit_factor() -> None:
    rows = [{"timestamp": 0}, {"timestamp": 30}]
    assert replay_speed(rows, replay_time=15.0) == 2.0
    assert replay_speed(rows) == 2.0
    assert replay_speed(rows, speed=1.0) == 1.0
    assert replay_speed(rows, speed=0.0) == 0.0


def test_format_replay_progress_prefers_trace_time() -> None:
    line = format_replay_progress_line(
        {
            "t0": 100.0,
            "t1": 130.0,
            "t": 115.0,
            "n": 10,
            "n_total": 20,
            "trees": {"runtime": {"n": 10, "n_total": 20}},
        },
        wall_s=2.0,
    )
    assert "15.0/30.0s" in line
    assert "10/20" in line
    assert "runtime" in line
    assert "wall 2.0s" in line
    lanes = live_fold_lanes("/tmp/events.jsonl")
    assert lanes[0]["source"] == "file"
    sources = {row["name"]: row["source"] for row in lanes}
    assert sources["chat"] == "live"
    assert sources["topology"] == "live"
    assert sources["series"] == "live"
    assert "story" not in sources


def test_describe_target_prints_duration(tmp_path: Path) -> None:
    src = tmp_path / "events.jsonl"
    src.write_text(
        '{"kind":"execution_start","agent_id":"entry","timestamp":0}\n'
        '{"kind":"llm_call_end","agent_id":"entry","timestamp":30}\n',
        encoding="utf-8",
    )
    rows = describe_target(src, replay_time=15.0)
    assert len(rows) == 1
    assert rows[0]["events"] == 2
    assert rows[0]["duration_s"] == 30.0
    assert rows[0]["speed"] == 2.0


def test_load_ui_folds_is_opt_in(monkeypatch) -> None:
    from mas.lab.controller.ui_stream.plot_folds import load_ui_folds

    monkeypatch.delenv("MAS_UI_FOLDS", raising=False)
    folds = load_ui_folds()
    assert all(hasattr(item, "apply") for item in folds)
