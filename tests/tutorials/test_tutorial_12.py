#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 12 — Spawned subagents: validation, template loading, dispatch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_openai import FakeOpenAI, last_role, offered_tools, text, tool_call
from tutorial_support import T12, load_yaml, run_cli, tutorial_copy


class TestManifestValidation:
    def test_validate_parent_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T12 / "agent.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_validate_reviewer_template(self):
        r = run_cli(["mas-ctl", "validate", str(T12 / "reviewer.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout


class TestManifestStructure:
    def test_parent_declares_spawn_subagent_with_budgets(self):
        m = load_yaml(T12 / "agent.yaml")
        plugins = m["spec"]["system_plugins"]
        assert plugins[0]["subagents"]["templates"][0]["id"] == "reviewer"
        params = plugins[0]["subagents"]
        assert params["templates"][0]["ref"] == "./reviewer.yaml"
        assert params["max_spawns"] == 8
        assert params["max_depth"] == 3

    def test_reviewer_is_a_normal_agent_manifest(self):
        m = load_yaml(T12 / "reviewer.yaml")
        assert m["kind"] == "Agent"
        assert m["spec"]["design_pattern"] == "react"


class TestTemplateLoading:
    """load_subagent_templates resolves and schema-validates the real fixture pair."""

    def test_loads_and_validates_the_reviewer_template(self):
        from mas.ctl.executor.subagent_spawner import load_subagent_templates

        manifest = load_yaml(T12 / "agent.yaml")
        templates = load_subagent_templates(manifest, T12)
        assert list(templates) == ["reviewer"]
        template = templates["reviewer"]
        assert template.description == "Review a proposed change."
        assert template.manifest["metadata"]["name"] == "reviewer"

    def test_rejects_a_ref_outside_the_manifest_root(self, tmp_path: Path):
        from mas.ctl.executor.subagent_spawner import load_subagent_templates

        manifest = {
            "spec": {
                "system_plugins": [
                    {"subagents": {"templates": [{"id": "escape", "ref": "../../etc/passwd", "description": "x"}]}}
                ]
            }
        }
        from mas.runtime.registry.provider_protocol import ManifestToolLoadError

        with pytest.raises(ManifestToolLoadError, match="escapes allowed roots"):
            load_subagent_templates(manifest, tmp_path)


class TestSpawnDispatch:
    """End-to-end engine-tool dispatch using the tutorial's own agent/reviewer pair."""

    async def test_spawn_subagent_runs_the_reviewer_template_and_tears_down(self):
        from mas.ctl.executor.engine_tool_context import MaterializedEngineToolContext  # noqa: F401
        from mas.ctl.executor.subagent_spawner import wire_subagent_spawning
        from mas.ctl.placement.bus.inproc import InProcessCommBus
        from mas.ctl.session.controller import TurnResult
        from mas.runtime.boundary.context.working_memory_registry import WorkingMemoryRegistry
        from mas.runtime.driver.driver import DriverTrace
        from mas.runtime.engine.tool_dispatch import aexecute_engine_tool
        from mas.runtime.schema.egress import EmitClientResponse
        from mas.runtime.system_plugins.spawn_ledger import SpawnLedger

        manifest = load_yaml(T12 / "agent.yaml")
        bus = InProcessCommBus()
        materialized = SimpleNamespace(instances={}, bus=bus)
        engine = SimpleNamespace(engine_tool_contracts=())
        controller_calls: list[tuple[str, str, str]] = []

        class Controller:
            def __init__(self, **_kwargs):
                pass

            async def arun_turn(self, task, *, turn_id, parent_call_id):
                controller_calls.append((task, turn_id, parent_call_id))
                return TurnResult(
                    trace=DriverTrace(),
                    responses=[EmitClientResponse(content="Looks correct and minimal.")],
                )

        instance = SimpleNamespace(
            driver=SimpleNamespace(agent_id="", ctx=SimpleNamespace(session_id="", agent_id="")),
            obs_plugin_set=None,
        )

        wire_subagent_spawning(
            engine,
            materialized=materialized,
            manifest=manifest,
            manifest_dir=T12,
            parent_agent_id="bounded-reviewer",
            session_id="session",
            working_memory_registry=WorkingMemoryRegistry(),
            ledger=SpawnLedger(max_depth=3, max_spawns=8),
            instance_factory=lambda _template, _agent_id: instance,
            controller_factory=Controller,
        )

        result = await aexecute_engine_tool(
            "create_subagent",
            engine_contracts=engine.engine_tool_contracts,
            arguments={"template": "reviewer", "task": "Review the new pagination code."},
            caller_call_id="parent-call",
        )

        assert result == "Looks correct and minimal."
        assert controller_calls[0][0] == "Review the new pagination code."
        # The child instance is torn down, never left registered.
        assert materialized.instances == {}


# ═══════════════════════════════════════════════════════════════════════════
# mas-ctl chat against a fake OpenAI endpoint
# ═══════════════════════════════════════════════════════════════════════════


def _delegate_script(body):
    """Parent calls create_subagent, the reviewer child answers, the parent relays the verdict."""
    if "create_subagent" not in offered_tools(body):
        return text("Reviewer verdict: LGTM, the function is correct.")
    if last_role(body) == "tool":
        return text(f"The reviewer said: {body['messages'][-1]['content']}")
    return tool_call("create_subagent", {"template": "reviewer", "task": "Check `def add(a, b): return a + b`."})


class TestSubagentChat:
    def test_chat_offers_create_subagent_to_the_model(self, tmp_path):
        """A grant in ``spec.system_plugins`` alone (no ``spec.tools``) puts the tool in the model request."""
        with FakeOpenAI(lambda _body: text("Nothing to review.")) as fake:
            r = run_cli(
                ["mas-ctl", "chat", "agent.yaml", "--no-cache-read", "--model", "gpt-4o", "-q", "Review my change."],
                cwd=tutorial_copy(T12, tmp_path),
                timeout=120,
                extra_env=fake.env(),
            )
        assert r.returncode == 0, r.stderr
        assert fake.bodies
        offered = fake.bodies[0]["tools"]
        spawn = next(tool["function"] for tool in offered if tool["function"]["name"] == "create_subagent")
        assert spawn["parameters"]["properties"]["template"]["enum"] == ["reviewer"]

    def test_parent_delegates_to_reviewer(self, tmp_path):
        with FakeOpenAI(_delegate_script) as fake:
            r = run_cli(
                [
                    "mas-ctl",
                    "chat",
                    "agent.yaml",
                    "--no-cache-read",
                    "--model",
                    "gpt-4o",
                    "-q",
                    "Ask the reviewer to check: `def add(a, b): return a + b`.",
                ],
                cwd=tutorial_copy(T12, tmp_path),
                timeout=120,
                extra_env=fake.env(),
            )
        assert r.returncode == 0, r.stderr
        assert "not wired" not in f"{r.stdout}{r.stderr}"
        tool_results = [str(b["messages"][-1]["content"]) for b in fake.bodies if last_role(b) == "tool"]
        assert tool_results
        assert "LGTM" in tool_results[0]
        assert "LGTM" in r.stdout
