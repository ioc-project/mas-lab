#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A run's fixtures start together unless one names another, and stop together."""

from __future__ import annotations

import contextvars
import os
import textwrap
import time
from pathlib import Path

import pytest
from mas.lab.benchmark.environment.attach import _fixture_waves, _in_parallel, start_run_fixtures, stop_run_fixtures
from mas.lab.benchmark.environment.fixtures import FixtureRegistry
from mas.lab.benchmark.environment.model import compile_environment

SERVER = textwrap.dedent(
    """
    import os, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    time.sleep(float(os.environ.get("BOOT_S", "0")))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
        def log_message(self, *a):
            return
    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
    """
)


def _spec(tmp_path: Path, fixtures: list[dict], boot_s: float = 0.0):
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    rows = [
        {
            "name": f["name"],
            "kind": "process",
            "scope": "run",
            "command": ["python3", str(script), *f.get("args", [])],
            "port": "auto",
            "health": {"path": "/", "timeout_s": 10},
            "env": {"BOOT_S": str(boot_s)},
        }
        for f in fixtures
    ]
    return compile_environment({"fixtures": rows})


def _names(waves: list[list]) -> list[list[str]]:
    return [[item.name for item in wave] for wave in waves]


def test_fixtures_that_name_nothing_in_common_share_a_wave(tmp_path: Path) -> None:
    spec = _spec(tmp_path, [{"name": "a"}, {"name": "b"}, {"name": "c"}])
    assert _names(_fixture_waves(spec.fixtures)) == [["a", "b", "c"]]


def test_a_fixture_waits_for_the_earlier_fixtures_it_names(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        [
            {"name": "issuer"},
            {"name": "verifier"},
            {"name": "payments", "args": ["--issuer", "${fixture.issuer.url}", "--v", "${fixtures.verifier.port}"]},
            {"name": "model"},
            {"name": "gate", "args": ["${fixture.payments.url}"]},
        ],
    )
    assert _names(_fixture_waves(spec.fixtures)) == [["issuer", "verifier", "model"], ["payments"], ["gate"]]


def test_a_name_no_earlier_fixture_carries_is_no_dependency(tmp_path: Path) -> None:
    spec = _spec(tmp_path, [{"name": "a", "args": ["${fixture.later.url}"]}, {"name": "later"}])
    assert _names(_fixture_waves(spec.fixtures)) == [["a", "later"]]


def test_independent_fixtures_start_and_stop_together(tmp_path: Path) -> None:
    boot = 0.6
    spec = _spec(tmp_path, [{"name": n} for n in "abcd"], boot_s=boot)
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    t0 = time.monotonic()
    start_run_fixtures(spec, registry, run_key="s/item1/r1", item_key="s/item1", scenario_key="s")
    up = time.monotonic() - t0
    try:
        assert up < 3 * boot, f"four fixtures of {boot}s each took {up:.2f}s: they started one after the other"
        assert {registry.url(n, scope_key="s/item1/r1") for n in "abcd"} and len(
            {registry.port(n, scope_key="s/item1/r1") for n in "abcd"}
        ) == 4
    finally:
        stop_run_fixtures(spec, registry, run_key="s/item1/r1", item_key="s/item1", scenario_key="s")
    assert not any(
        registry.has(n, scope_key="s/item1/r1") and registry._find(n, scope_key="s/item1/r1").started for n in "abcd"
    )


def test_a_dependent_fixture_gets_the_address_of_the_one_it_names(tmp_path: Path) -> None:
    spec = _spec(tmp_path, [{"name": "issuer"}, {"name": "payments", "args": ["${fixture.issuer.url}"]}])
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    start_run_fixtures(spec, registry, run_key="s/item1/r1", item_key="s/item1", scenario_key="s")
    try:
        issuer = registry.url("issuer", scope_key="s/item1/r1")
        assert issuer in registry._find("payments", scope_key="s/item1/r1").command
    finally:
        stop_run_fixtures(spec, registry, run_key="s/item1/r1", item_key="s/item1", scenario_key="s")


def test_a_failed_fixture_raises_after_the_others_of_its_wave_ended(tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    spec = compile_environment(
        {
            "fixtures": [
                {
                    "name": "good",
                    "kind": "process",
                    "scope": "run",
                    "command": ["python3", str(script)],
                    "port": "auto",
                    "health": {"path": "/", "timeout_s": 10},
                },
                {
                    "name": "bad",
                    "kind": "process",
                    "scope": "run",
                    "command": ["python3", "-c", "raise SystemExit(3)"],
                    "port": "auto",
                    "health": {"path": "/", "timeout_s": 10},
                },
            ]
        }
    )
    registry = FixtureRegistry(base_env=dict(os.environ), output_dir=tmp_path, work_dir=tmp_path)
    try:
        with pytest.raises(RuntimeError, match="bad"):
            start_run_fixtures(spec, registry, run_key="s/item1/r1", item_key="s/item1", scenario_key="s")
        assert registry._find("good", scope_key="s/item1/r1").started
    finally:
        stop_run_fixtures(spec, registry, run_key="s/item1/r1", item_key="s/item1", scenario_key="s")


def test_each_parallel_call_sees_the_context_of_its_caller() -> None:
    run = contextvars.ContextVar("run", default="")
    run.set("a/item1/r1")
    seen: list[str] = []
    _in_parallel([lambda: seen.append(run.get()), lambda: seen.append(run.get())])
    assert seen == ["a/item1/r1", "a/item1/r1"]
