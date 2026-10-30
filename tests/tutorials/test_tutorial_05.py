#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 05 — A2A agents: expose one specialist over A2A, keep the rest
local, offline — using the tutorial's own cited infra manifest and
Tutorial 02's real MAS.

Tutorial 5 has no files of its own: it reuses Tutorial 2's MAS/agents plus
library-samples/infra/mixed-agents.infra.yaml.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from conftest import REPO_ROOT, T02, load_yaml, run_cli

INFRA = REPO_ROOT / "library-samples" / "infra" / "mixed-agents.infra.yaml"


class TestManifestValidation:
    def test_validate_mas(self):
        r = run_cli(["mas-ctl", "validate", str(T02 / "mas.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_validate_schedule_agent(self):
        r = run_cli(
            ["mas-ctl", "validate", str(T02 / "agents" / "schedule-agent" / "agent.yaml")]
        )
        assert r.returncode == 0, r.stderr


class TestInfraManifest:
    def test_mixed_agents_infra_routes_schedule_agent_through_a2a(self):
        infra = load_yaml(INFRA)
        endpoint = infra["spec"]["endpoints"]["schedule_agent"]
        assert endpoint["protocol"] == "a2a"
        assert endpoint["usage"] == ["connect", "listen"]
        assert endpoint["url"].startswith("http://127.0.0.1:")


class TestMixedTopologyEndToEnd:
    """The MAS graph is unchanged; only schedule_agent crosses an A2A endpoint."""

    def test_schedule_agent_serves_its_real_agentcard_while_others_stay_local(self):
        import httpx
        from mas.ctl.executor.run_mas import execute_run_mas
        from mas.ctl.session.engine_factory import EngineSelection
        from mas.runtime.engine.simulated import SimulatedEngine

        infra = load_yaml(INFRA)
        port = int(infra["spec"]["endpoints"]["schedule_agent"]["url"].rsplit(":", 1)[1])
        cards: dict[str, object] = {}

        sel = EngineSelection(
            engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="Here is your plan."),
            mode="injected",
        )

        def _run_turn(*_args: object, **_kwargs: object):
            response = httpx.get(
                f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=5
            )
            assert response.status_code == 200, response.text
            cards["schedule_agent"] = response.json()
            return SimpleNamespace(
                text="Here is your plan.",
                awaiting_hitl=False,
                trace=SimpleNamespace(client_responses=[], boundary_errors=[]),
                responses=[],
            )

        with patch("mas.ctl.session.bootstrap.build_engine", return_value=sel):
            with patch(
                "mas.ctl.session.controller.SessionController.run_turn", side_effect=_run_turn
            ):
                rc = execute_run_mas(
                    T02 / "mas.yaml",
                    prompt="Plan a trip and compare the available transport schedules.",
                    validate=False,
                    infra_refs=[str(INFRA)],
                    auto_hitl=True,
                )

        assert rc == 0
        assert cards["schedule_agent"]["name"] == "schedule_agent"


class TestServeCommandOffline:
    """``mas-ctl serve`` (the README's own command) exposes the AgentCard.

    No model call happens while idle, so this needs no LLM credentials —
    unlike ``mas-ctl chat``/``run-mas`` with a live query.
    """

    def test_serve_and_fetch_agentcard(self):
        import subprocess
        import sys
        import time
        from pathlib import Path as _Path

        import httpx

        infra = load_yaml(INFRA)
        port = int(infra["spec"]["endpoints"]["schedule_agent"]["url"].rsplit(":", 1)[1])

        import os

        venv_bin = _Path(sys.executable).parent
        exe = venv_bin / "mas-ctl"
        env = {**os.environ, "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or "sk-test-not-called"}
        proc = subprocess.Popen(
            [
                str(exe) if exe.exists() else "mas-ctl",
                "serve",
                str(T02 / "agents" / "schedule-agent" / "agent.yaml"),
                "--infra-ref",
                str(INFRA),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            deadline = time.monotonic() + 20
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    r = httpx.get(
                        f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=1
                    )
                    if r.status_code == 200:
                        assert r.json()["name"] == "schedule_agent"
                        return
                except httpx.HTTPError as exc:
                    last_error = exc
                time.sleep(0.5)
            raise AssertionError(f"server never listened on {port}: {last_error}")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
