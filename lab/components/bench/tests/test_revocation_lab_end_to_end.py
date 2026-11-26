#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
"""The offline revocation lab through the real batch path: fixtures, kernel, triggers, virtual clock.

Both batches run inside one test: the plugin registry is reset between tests.

Unit tests of the engine never ran a step on the worker thread the kernel's event loop forces; a
lock taken by a clock-advancing step deadlocked only here.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from mas.lab.benchmark.schedule.run_batch import run_mas_benchmark


@pytest.fixture(autouse=True)
def _scripted_model_key(tmp_path: Path, monkeypatch) -> None:
    """The lab answers from its scripted-model fixture; the key only has to exist for the live-LLM check."""
    from mas.ctl.secrets import write_file_secret

    secrets = tmp_path / "secrets"
    write_file_secret(secrets, "lab", "offline-scripted-model")
    write_file_secret(secrets, "openai", "offline-scripted-model")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.yaml").write_text(f"secrets:\n  dir: {secrets}\n", encoding="utf-8")
    monkeypatch.setenv("MAS_WORKSPACE_ROOT", str(ws))


LAB = Path(__file__).resolve().parents[4] / "labs" / "revocation.lab"


def _errors(output: Path) -> list[str]:
    import csv

    path = output / "results.csv"
    if not path.is_file():
        return ["no results.csv"]
    return [f"{r['scenario']}/{r['item_id']}: {r['error']}" for r in csv.DictReader(path.open()) if r["status"] != "ok"]


def _records(trace_cache: Path) -> list[list[dict]]:
    out = []
    for path in sorted(trace_cache.glob("*/traces/environment.jsonl")):
        out.append([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])
    return out


@pytest.mark.timeout(240)
def test_revocation_lab_runs_in_parallel_with_own_fixtures_and_a_rerun_is_cached(tmp_path: Path, monkeypatch) -> None:
    import yaml
    from mas.runtime import registry as plugin_registry

    # A fresh registry for this lab, put back afterwards: other tests build theirs differently.
    monkeypatch.setattr(plugin_registry, "_instance", None)
    lab = tmp_path / "revocation.lab"
    shutil.copytree(LAB, lab)
    config = yaml.safe_load((lab / "experiment.yaml").read_text(encoding="utf-8"))
    first = config["experiment"]["scenarios"][0]
    # A different environment gives a different run hash; identical runs would share one cached trace.
    second = {**first, "id": "issuer-backed-b", "environment": {"limits": {"max_sleep_s": 61}}}
    config["experiment"]["scenarios"].append(second)
    config["experiment"]["schedule"]["parallel_scenarios"] = 2
    (lab / "experiment.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.chdir(lab)
    trace_cache = tmp_path / "traces"
    kwargs = {
        "fixture_env": dict(os.environ),
        "progress": False,
        "trace_cache_dir": trace_cache,
        "output_dir": tmp_path / "out",
    }

    first_ok = asyncio.run(asyncio.wait_for(run_mas_benchmark(lab / "experiment.yaml", force=True, **kwargs), 200))
    assert first_ok is True, _errors(tmp_path / "out")
    runs = _records(trace_cache)
    assert len(runs) == 4  # two scenarios x two items, each with its own fixture set
    ports = [rec["payload"]["port"] for run in runs for rec in run if rec["name"] == "fixture.ready"]
    assert len(ports) == len(set(ports)) == 12
    for run in runs:
        assert run[-1]["name"] == "environment.closed"
        names = [rec["name"] for rec in run]
        assert "action.failed" not in names and "action.timeout" not in names
        started = {rec["source"]["id"] for rec in run if rec["name"] == "fixture.ready"}
        stopped = {rec["source"]["id"] for rec in run if rec["name"] == "fixture.stopped"}
        assert started == stopped == {"issuer", "payments", "scripted-model"}, names
    # the wait cell: revoke after tool result 2, then the virtual clock moves; every action ends
    wait_cells = [run for run in runs if any(rec["name"] == "clock.advanced" for rec in run)]
    assert len(wait_cells) == 2
    for run in wait_cells:
        names = [rec["name"] for rec in run]
        assert names.count("trigger.matched") == 2 and names.count("action.done") == 2

    before = {p: p.stat().st_mtime_ns for p in trace_cache.glob("*/traces/environment.jsonl")}
    second_ok = asyncio.run(asyncio.wait_for(run_mas_benchmark(lab / "experiment.yaml", **kwargs), 200))
    assert second_ok is True, _errors(tmp_path / "out")
    after = {p: p.stat().st_mtime_ns for p in trace_cache.glob("*/traces/environment.jsonl")}
    assert before == after  # a cache hit replays the file; it starts no fixture and rewrites nothing
    # Editing a fixture script changes the run hash: without --force the runs happen again, they are
    # not served from the output folder that still links to the old hash.
    (lab / "fixtures" / "issuer.py").write_text(
        (lab / "fixtures" / "issuer.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"
    )
    edited_ok = asyncio.run(asyncio.wait_for(run_mas_benchmark(lab / "experiment.yaml", **kwargs), 200))
    assert edited_ok is True, _errors(tmp_path / "out")
    assert len(_records(trace_cache)) == 2 * len(runs)  # new traces beside the old ones
    # The same experiment forced again in this process starts its streams over instead of failing.
    third_ok = asyncio.run(asyncio.wait_for(run_mas_benchmark(lab / "experiment.yaml", force=True, **kwargs), 200))
    assert third_ok is True, _errors(tmp_path / "out")
    left = subprocess.run(["pgrep", "-f", str(lab / "fixtures")], capture_output=True, text=True).stdout.strip()
    assert left == ""


@pytest.mark.timeout(120)
def test_wall_timers_event_chain_and_gdb_script_run_through_the_batch(tmp_path: Path, monkeypatch) -> None:
    """Wall timers, an `after` timer, an event trigger and a gdb script, each of which only the real
    batch path exercises (the generated Control must pass the Control loader)."""
    import yaml
    from mas.runtime import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "_instance", None)
    lab = tmp_path / "revocation.lab"
    shutil.copytree(LAB, lab)
    config = yaml.safe_load((lab / "experiment.yaml").read_text(encoding="utf-8"))
    env = config["experiment"]["environment"]
    env["clock"] = {"kind": "wall"}
    env["timers"] = [
        {"id": "tick", "at": "0.05s", "do": [{"type": "emit", "config": {"name": "tick"}}]},
        {"id": "later", "after": "tick", "delay": "0.05s", "do": [{"type": "emit", "config": {"name": "later"}}]},
    ]
    env["triggers"] = [
        {
            "id": "on-tick",
            "when": {"on": "event", "event": "tick"},
            "do": [{"type": "emit", "config": {"name": "saw"}}],
        },
        {
            "id": "slow",
            "when": {"on": "tool_call", "tool": "pay_fare", "occurrence": 1},
            "do": [{"type": "sleep", "config": {"for": 1}}],
        },
    ]
    env["script"] = "break tool_result pay_fare if 1\ncommands\nemit from-gdb\nend\n"
    (lab / "experiment.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    dataset = yaml.safe_load((lab / "dataset.yaml").read_text(encoding="utf-8"))
    dataset["spec"]["items"] = [{"id": "only", "inputs": dataset["spec"]["items"][0]["inputs"]}]
    (lab / "dataset.yaml").write_text(yaml.safe_dump(dataset), encoding="utf-8")
    monkeypatch.chdir(lab)
    output = tmp_path / "out"

    ok = asyncio.run(
        asyncio.wait_for(
            run_mas_benchmark(
                lab / "experiment.yaml",
                fixture_env=dict(os.environ),
                progress=False,
                force=True,
                trace_cache_dir=tmp_path / "traces",
                output_dir=output,
            ),
            timeout=100,
        )
    )
    assert ok is True, _errors(output)
    (log,) = _records(tmp_path / "traces")
    names = [rec["name"] for rec in log]
    fired = [rec["source"]["id"] for rec in log if rec["name"] == "timer.fired"]
    assert fired == ["tick", "later"]  # `later` waited for `tick`, then its own delay
    assert "user.from-gdb" in names  # the gdb script ran its command
    assert any(rec["name"] == "trigger.matched" and rec["source"]["id"] == "on-tick" for rec in log)  # event chain
    assert "action.failed" not in names and "action.timeout" not in names


@pytest.mark.timeout(120)
def test_session_actions_run_against_the_real_session_manager(tmp_path: Path, monkeypatch) -> None:
    """steer, inject, checkpoint + resume and pause + resume through the real control API."""
    import yaml
    from mas.runtime import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "_instance", None)
    lab = tmp_path / "revocation.lab"
    shutil.copytree(LAB, lab)
    config = yaml.safe_load((lab / "experiment.yaml").read_text(encoding="utf-8"))
    on = {"on": "tool_result", "tool": "pay_fare"}
    config["experiment"]["environment"]["triggers"] = [
        {
            "id": "steer",
            "when": {**on, "occurrence": 1},
            "do": [{"type": "steer", "config": {"text": "be brief"}}, {"type": "inject", "config": {"text": "ping"}}],
        },
        {
            "id": "ckpt",
            "when": {**on, "occurrence": 2},
            "do": [{"type": "checkpoint", "config": {"label": "mid"}}, {"type": "resume", "config": {}}],
        },
        {
            "id": "pause",
            "when": {**on, "occurrence": 3},
            "do": [{"type": "pause", "config": {"reason": "check"}}, {"type": "resume", "config": {}}],
        },
    ]
    (lab / "experiment.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    dataset = yaml.safe_load((lab / "dataset.yaml").read_text(encoding="utf-8"))
    dataset["spec"]["items"] = [{"id": "only", "inputs": dataset["spec"]["items"][0]["inputs"]}]
    (lab / "dataset.yaml").write_text(yaml.safe_dump(dataset), encoding="utf-8")
    monkeypatch.chdir(lab)
    output = tmp_path / "out"

    ok = asyncio.run(
        asyncio.wait_for(
            run_mas_benchmark(
                lab / "experiment.yaml",
                fixture_env=dict(os.environ),
                progress=False,
                force=True,
                trace_cache_dir=tmp_path / "traces",
                output_dir=output,
            ),
            timeout=100,
        )
    )
    assert ok is True, _errors(output)
    (log,) = _records(tmp_path / "traces")
    steps = [(rec["source"]["id"], rec["source"]["step"]) for rec in log if rec["name"] == "action.done"]
    assert steps == [
        ("steer", "steer"),
        ("steer", "inject"),
        ("ckpt", "checkpoint"),
        ("ckpt", "resume"),
        ("pause", "pause"),
        ("pause", "resume"),
    ]
    assert not [rec for rec in log if rec["name"] in {"action.failed", "action.timeout"}]


@pytest.mark.timeout(120)
def test_kill_switch_cancels_a_decode_that_is_in_flight(tmp_path: Path, monkeypatch) -> None:
    """The one place a trigger can end a turn: a wall timer fires while the model is still deciding."""
    import csv

    import yaml
    from mas.runtime import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "_instance", None)
    lab = tmp_path / "revocation.lab"
    shutil.copytree(LAB, lab)
    config = yaml.safe_load((lab / "experiment.yaml").read_text(encoding="utf-8"))
    env = config["experiment"]["environment"]
    env["clock"] = {"kind": "wall"}
    for fixture in env["fixtures"]:
        if fixture["name"] == "scripted-model":
            fixture["command"] = ["python3", "fixtures/scripted_model.py", "--port", "{port}", "--delay-s", "3"]
    env["timers"] = [{"id": "kill", "at": "1s", "do": [{"type": "kill_switch", "config": {"reason": "stop-now"}}]}]
    env["triggers"] = []
    (lab / "experiment.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    dataset = yaml.safe_load((lab / "dataset.yaml").read_text(encoding="utf-8"))
    dataset["spec"]["items"] = [{"id": "only", "inputs": dataset["spec"]["items"][0]["inputs"]}]
    (lab / "dataset.yaml").write_text(yaml.safe_dump(dataset), encoding="utf-8")
    monkeypatch.chdir(lab)
    output = tmp_path / "out"

    ok = asyncio.run(
        asyncio.wait_for(
            run_mas_benchmark(
                lab / "experiment.yaml",
                fixture_env=dict(os.environ),
                progress=False,
                force=True,
                trace_cache_dir=tmp_path / "traces",
                output_dir=output,
            ),
            timeout=100,
        )
    )
    assert ok is True, _errors(output)
    (log,) = _records(tmp_path / "traces")
    done = next(rec for rec in log if rec["name"] == "action.done")
    assert done["payload"]["cancelled"] == 1  # the decode was in flight and was cancelled
    row = next(csv.DictReader((output / "results.csv").open()))
    assert "cancel" in row["output"].lower()  # the turn ended on the cancel; it never reached a tool call
    events = (next((tmp_path / "traces").glob("*/traces/events.jsonl"))).read_text(encoding="utf-8")
    assert '"tool_call_start"' not in events


@pytest.mark.timeout(120)
def test_an_infra_file_names_the_runs_own_fixture(tmp_path: Path, monkeypatch) -> None:
    """``${fixture.<name>.url}`` in a scenario's infra file is filled in per run, after the run's fixtures are up."""
    import yaml
    from mas.runtime import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "_instance", None)
    lab = tmp_path / "revocation.lab"
    shutil.copytree(LAB, lab)
    (lab / "llm.infra.yaml").write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: extra-leaf}\nspec:\n  protocol: openai\n"
        '  proxy: {api_base: "${fixture.payments.url}/v1", api_key: {secret: {name: lab, optional: true}}}\n'
        "  models: {allowed: [never-called], mappings: {never-called: never-called}}\n",
        encoding="utf-8",
    )
    config = yaml.safe_load((lab / "experiment.yaml").read_text(encoding="utf-8"))
    config["experiment"]["scenarios"][0]["infra_refs"] = ["llm.infra.yaml"]
    (lab / "experiment.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.chdir(lab)
    trace_cache = tmp_path / "traces"
    ok = asyncio.run(
        asyncio.wait_for(
            run_mas_benchmark(
                lab / "experiment.yaml",
                force=True,
                fixture_env=dict(os.environ),
                progress=False,
                trace_cache_dir=trace_cache,
                output_dir=tmp_path / "out",
            ),
            100,
        )
    )
    assert ok is True, _errors(tmp_path / "out")
    rendered = sorted((tmp_path / "out").rglob(".environment/infra/*llm.infra.yaml"))
    assert rendered  # one copy per run
    for path in rendered:
        assert "${" not in path.read_text(encoding="utf-8")
    ports = {
        str(rec["payload"]["port"])
        for run in _records(trace_cache)
        for rec in run
        if rec["name"] == "fixture.ready" and rec["source"]["id"] == "payments"
    }
    urls = [yaml.safe_load(path.read_text(encoding="utf-8"))["spec"]["proxy"]["api_base"] for path in rendered]
    assert {url.split(":")[-1].removesuffix("/v1") for url in urls} == ports  # each run reached its own instance
    assert len(set(urls)) == len(urls) == len(rendered)
