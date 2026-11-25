#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A host hands overrides over explicitly; library code neither reads nor writes the environment."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.ctl.workspace.config import bind_infra_refs, bound_infra_refs
from mas.lab.benchmark.engine import BenchmarkRunOptions
from mas.lab.benchmark.host_overrides import apply, capture
from mas.lab.benchmark.schedule.run_batch.execute import _infra_refs_for_scenario
from mas.lab.benchmark.schedule.run_batch.prepare import extra_overlays_for_scenario
from mas.lab.controller.workers import _run_spec_with_demo_ui
from mas.library.standard.lib import llm_scheduler
from mas.runtime.engine import llm_cache


@pytest.fixture(autouse=True)
def _clean():
    llm_scheduler.install_overrides(None)
    before = llm_cache.snapshot_paths()
    yield
    llm_scheduler.install_overrides(None)
    llm_cache._PATHS.update(before)


def test_worker_spec_sets_no_environment_variable() -> None:
    before = dict(os.environ)
    live = _run_spec_with_demo_ui(
        {
            "experiment_yaml": "x.yaml",
            "demo_ui_ingest_url": "http://u/api",
            "demo_ui_ref": {"a": 1},
            "query": "q",
            "infra_refs": ["a", "b"],
            "scenario_infra_refs": {"s": ["c"]},
            "cwd": "/w",
        }
    )
    assert dict(os.environ) == before
    assert live["workspace_infra_refs"] == ["a", "b"]
    assert live["llm_cache_path"] == str(Path("/w/infra/.cache/eval.llm-cache.json"))
    assert live["progress"] is False


def test_worker_spec_infra_refs_cases() -> None:
    assert _run_spec_with_demo_ui({"experiment_yaml": "x"})["workspace_infra_refs"] == []
    assert "workspace_infra_refs" not in _run_spec_with_demo_ui({"experiment_yaml": "x", "keep_process_infra": True})


def test_options_carry_demo_ui() -> None:
    opts = BenchmarkRunOptions.from_spec(
        {
            "demo_ui_ingest_url": "http://u/api",
            "scenario_overlays": {"s": ["o"]},
            "scenario_infra_refs": {"s": ["i"]},
            "workspace_infra_refs": [],
            "llm_cache_path": "/c.json",
        }
    )
    assert opts.demo_ui == {
        "ingest_url": "http://u/api",
        "scenario_overlays": {"s": ["o"]},
        "scenario_infra_refs": {"s": ["i"]},
        "llm_cache_path": "/c.json",
        "workspace_infra_refs": [],
    }
    assert BenchmarkRunOptions.from_spec({}).demo_ui is None


def test_scenario_mappings_are_explicit() -> None:
    assert extra_overlays_for_scenario("S1", {"s1": [" o1 ", ""]}) == ["o1"]
    assert extra_overlays_for_scenario("S1", None) == []
    prepared = SimpleNamespace(infra_refs=["base"], scenario_infra_refs={"S1": ["x", "y"]})
    assert _infra_refs_for_scenario("S1", prepared) == ["x", "y"]
    assert _infra_refs_for_scenario("S2", prepared) == ["base"]


def test_capture_and_apply_round_trip_for_a_spawned_worker() -> None:
    llm_scheduler.install_overrides({"max_in_flight": 3})
    with bind_infra_refs(["r1"]), llm_cache.bind_paths(cli="/cli.json", default="/d.json"):
        host = capture()

    assert host.pop("overlay") == {}
    assert host == {
        "scheduler": {"max_in_flight": 3},
        "workspace_infra_refs": ["r1"],
        "llm_cache": {"cli": "/cli.json", "default": "/d.json"},
    }
    llm_scheduler.install_overrides(None)
    assert bound_infra_refs() is None
    with apply(host):
        assert llm_scheduler.current_overrides() == {"max_in_flight": 3}
        assert bound_infra_refs() == ["r1"]
        assert llm_cache.snapshot_paths()["cli"] == "/cli.json"
    assert llm_scheduler.current_overrides() == {}
    assert bound_infra_refs() is None
    assert llm_cache.snapshot_paths()["cli"] is None
    with apply(None):
        assert bound_infra_refs() is None


def test_service_children_start_from_the_host_environment() -> None:
    from mas.lab.benchmark import service_manager as sm
    from mas.runtime import hostenv

    hostenv.install({"PATH": "/bin", "KEEP": "1"})
    try:
        with hostenv.overlay({"EXPORTED": "x"}):
            # Allowlist (PATH) plus declared extras. KEEP and overlay keys stay out.
            mgr = sm.ServiceManager(Path("/nonexistent/services.yaml"))
            svc = sm.ServiceDef(name="s", env={"A": "b"})
            assert mgr._compose_env(svc) == {"PATH": "/bin", "A": "b"}
    finally:
        hostenv.install(os.environ)


def test_run_scoped_bindings_do_not_leak_between_concurrent_runs() -> None:
    """Two runs in one process (the controller daemon) each see their own bindings."""
    import threading

    from mas.library.standard.lib import llm_scheduler
    from mas.runtime.engine import llm_cache

    seen: dict[str, tuple] = {}
    both_bound = threading.Barrier(2)

    def run(name: str) -> None:
        with bind_infra_refs([name]), llm_cache.bind_paths(default=f"/{name}.json"):
            with llm_scheduler.default_user(name):
                both_bound.wait(timeout=5)
                seen[name] = (
                    bound_infra_refs(),
                    llm_cache.snapshot_paths()["default"],
                    llm_scheduler.current_overrides().get("user"),
                )
                both_bound.wait(timeout=5)

    threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"a": (["a"], "/a.json", "a"), "b": (["b"], "/b.json", "b")}
    assert bound_infra_refs() is None
    assert llm_cache.snapshot_paths()["default"] is None
    assert "user" not in llm_scheduler.current_overrides()


def test_the_default_scheduler_user_does_not_stick_to_the_process() -> None:
    from mas.library.standard.lib import llm_scheduler

    with llm_scheduler.default_user("first-experiment"):
        assert llm_scheduler.current_overrides()["user"] == "first-experiment"
        assert "user" not in llm_scheduler.explicit_overrides()
    assert llm_scheduler.current_overrides().get("user") is None


def test_a_job_carries_exports_not_the_whole_environment() -> None:
    from mas.runtime import hostenv

    with hostenv.overlay({"EXPORTED_ENDPOINT": "http://x"}):
        host = capture()
    assert host["overlay"] == {"EXPORTED_ENDPOINT": "http://x"}
    assert "env" not in host
    assert "PATH" not in str(host)


def test_a_run_without_the_cli_environment_gives_fixtures_the_process_allowlist() -> None:
    from mas.lab.benchmark.schedule.run_batch.prepare import fixture_base_env
    from mas.runtime import hostenv

    with hostenv.overlay({"EXPORTED": "x"}):
        env = fixture_base_env(None)
        assert "EXPORTED" not in env
        assert "PATH" in env
    assert fixture_base_env({"ONLY": "1"}) == {"ONLY": "1"}
    assert fixture_base_env({}) == {}
