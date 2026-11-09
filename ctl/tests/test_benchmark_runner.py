#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Bench runner identity — stable runner_id for analytics."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mas.ctl.benchmark.runner import MasBenchRunner, select_mas_runner
from mas.ctl.benchmark.runner_dispatch import is_mas_manifest_kind, mas_manifest_path
from mas.ctl.compose.models import AgentBindSlice, EffectiveBindManifest, PlacementPlan
from mas.ctl.compose.runner import ComposeResult
from mas.ctl.executor.mas_session import MaterializedMas, agent_manifest_label
from mas.lab.manifest.load import entry_agent_from_compose
from mas.lab.runners.constants import DEFAULT_LAB_RUNNER_ID
from mas.lab.runners.protocol import RunResult


def test_mas_bench_runner_id_is_mas_lab():
    """Bench runner uses stable runner_id 'mas-lab' for analytics."""
    assert MasBenchRunner.runner_id == DEFAULT_LAB_RUNNER_ID
    assert select_mas_runner().runner_id == DEFAULT_LAB_RUNNER_ID


def test_is_mas_manifest_kind_by_kind_not_filename(tmp_path: Path):
    """MAS manifest detection uses kind field, not filename."""
    triage = tmp_path / "triage.yaml"
    triage.write_text("kind: mas\n", encoding="utf-8")
    config = {"kind": "mas", "metadata": {"name": "triage"}}
    assert is_mas_manifest_kind(config, triage) is True
    assert mas_manifest_path(config, triage) == triage


def test_is_mas_manifest_kind_rejects_agent_kind(tmp_path: Path):
    """Agent manifests correctly rejected by MAS kind detector."""
    agent = tmp_path / "moderator.yaml"
    agent.write_text("kind: agent\n", encoding="utf-8")
    config = {"kind": "agent", "metadata": {"name": "moderator"}}
    assert is_mas_manifest_kind(config, agent) is False
    assert mas_manifest_path(config, agent) is None


def test_entry_agent_label_from_loaded_mas_runtime_dict(tmp_path: Path):
    agent_path = tmp_path / "agent.yaml"
    agent_path.write_text("kind: agent\nmetadata:\n  name: qa-agent\n", encoding="utf-8")
    runtime_cfg = {
        "_loaded_mas_raw": True,
        "mas": {"entry_agent": "qa-agent"},
        "agents": [{"id": "qa-agent", "name": "qa-agent"}],
    }
    assert agent_manifest_label(runtime_cfg, agent_path) == "qa-agent"


def test_mas_bench_runner_passes_pattern_plugin_id_from_manifest(tmp_path: Path):
    """Design pattern from manifest propagates to runtime options."""
    manifest = {
        "metadata": {"name": "agent"},
        "spec": {"design_pattern": {"type": "cot"}},
    }
    agent_path = tmp_path / "agent.yaml"
    agent_path.write_text("kind: agent\n", encoding="utf-8")

    with patch("mas.ctl.benchmark.runner.instantiate_runtime") as inst:
        inst.return_value = (object(), None)
        with patch.object(MasBenchRunner, "_run_controller_turns") as turns:
            turns.return_value = RunResult(content="ok")
            MasBenchRunner().run(
                "hello",
                config=manifest,
                spec_path=agent_path,
                output_dir=tmp_path / "out",
            )
    options = inst.call_args[0][0]
    assert options.pattern_plugin_id == "cot@v1"


def test_mas_bench_compose_called_once_for_mas_kind(tmp_path: Path):
    mas_path = tmp_path / "workflow.yaml"
    mas_path.write_text(
        "kind: mas\nmetadata:\n  name: demo\nspec:\n  workflow:\n    entry: alpha\n",
        encoding="utf-8",
    )
    agent_path = tmp_path / "agents" / "alpha.yaml"
    agent_path.parent.mkdir()
    agent_path.write_text(
        "kind: agent\nmetadata:\n  name: alpha\nspec:\n  design_pattern:\n    type: react\n",
        encoding="utf-8",
    )

    compose = ComposeResult(
        mas_id="demo",
        mas_config={
            "kind": "mas",
            "metadata": {"name": "demo"},
            "spec": {
                "workflow": {"entry": "alpha"},
                "agency": {"agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}]},
            },
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )

    with patch("mas.ctl.benchmark.runner.compose_run", return_value=compose) as compose_run:
        with patch("mas.ctl.benchmark.runner.instantiate_runtime") as inst:
            inst.return_value = (object(), None)
            with patch.object(MasBenchRunner, "_run_controller_turns") as turns:
                turns.return_value = RunResult(content="ok")
                MasBenchRunner().run(
                    "hello",
                    config=compose.mas_config,
                    spec_path=mas_path,
                    output_dir=tmp_path / "out",
                )
    assert compose_run.call_count == 1


def test_mas_bench_runner_attaches_cli_controls(tmp_path: Path):
    mas_path = tmp_path / "workflow.yaml"
    mas_path.write_text(
        "kind: mas\nmetadata:\n  name: demo\nspec:\n  workflow:\n    entry: alpha\n",
        encoding="utf-8",
    )
    agent_path = tmp_path / "agents" / "alpha.yaml"
    agent_path.parent.mkdir()
    agent_path.write_text(
        "kind: agent\nmetadata:\n  name: alpha\nspec:\n  design_pattern:\n    type: react\n",
        encoding="utf-8",
    )
    gov = tmp_path / "gov.yaml"
    gov.write_text(
        "apiVersion: control/v1\nkind: Control\nmetadata: {name: gov}\nspec:\n  governance: [sample_governance]\n",
        encoding="utf-8",
    )
    compose = ComposeResult(
        mas_id="demo",
        mas_config={
            "kind": "mas",
            "metadata": {"name": "demo"},
            "spec": {
                "workflow": {"entry": "alpha"},
                "agency": {"agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}]},
            },
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )
    captured: dict = {}

    def _bind(resolved, flavour, **kwargs):
        captured["flavour"] = flavour
        from mas.ctl.compose.models import ResolvedInfra

        return resolved or ResolvedInfra()

    with patch("mas.ctl.benchmark.runner.compose_run", return_value=compose):
        with patch("mas.ctl.benchmark.runner.bind_session", side_effect=_bind):
            with patch("mas.ctl.benchmark.runner.instantiate_runtime") as inst:
                inst.return_value = (object(), None)
                with patch.object(MasBenchRunner, "_run_controller_turns") as turns:
                    turns.return_value = RunResult(content="ok")
                    MasBenchRunner().run(
                        "hello",
                        config=compose.mas_config,
                        spec_path=mas_path,
                        output_dir=tmp_path / "out",
                        flavour={},
                        controls=[f"{gov.name}@.mas.agent[*]"],
                        workspace_root=tmp_path,
                    )
    from mas.ctl.infra.bind import flavour_spec_of

    spec = flavour_spec_of(captured["flavour"])
    assert spec["_controls"][0]["name"] == "gov"
    assert spec["_controls"][0]["agents"] == ".mas.agent[*]"


def test_attach_run_controls_does_not_stick_on_the_shared_flavour(tmp_path: Path) -> None:
    from mas.ctl.infra.bind import flavour_spec_of
    from mas.ctl.session.flavour_control import session_flavour_with_controls

    def _gov(name: str) -> Path:
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            f"apiVersion: control/v1\nkind: Control\nmetadata: {{name: {name}}}\n"
            "spec:\n  governance: [sample_governance]\n",
            encoding="utf-8",
        )
        return path

    flavour: dict = {}
    first = session_flavour_with_controls(flavour, overlay_paths=[_gov("a")])
    second = session_flavour_with_controls(flavour, overlay_paths=[_gov("b")])
    assert flavour_spec_of(flavour).get("_controls") in (None, [])
    assert flavour_spec_of(first)["_controls"][0]["name"] == "a"
    assert flavour_spec_of(second)["_controls"][0]["name"] == "b"


def test_standalone_unknown_control_is_a_run_result(tmp_path: Path) -> None:
    agent = tmp_path / "desk.yaml"
    agent.write_text(
        "kind: agent\nmetadata:\n  name: desk\nspec:\n  description: x\n",
        encoding="utf-8",
    )
    result = MasBenchRunner().run(
        "hello",
        config={"kind": "agent", "metadata": {"name": "desk"}, "spec": {"description": "x"}},
        spec_path=agent,
        output_dir=tmp_path / "out",
        flavour={},
        controls=["nowhere.yaml"],
        workspace_root=tmp_path,
    )
    assert result.status == "error"
    assert "nowhere.yaml" in (result.error or "")
    assert "Traceback" not in (result.error or "")


def test_multi_agent_topology_runs_on_unified_controller_path(tmp_path: Path):
    """Multi-agent topologies use unified SessionController path."""
    mas_path = tmp_path / "mas.yaml"
    agent_path = tmp_path / "agents" / "step-a.yaml"
    agent_path.parent.mkdir()
    agent_path.write_text("kind: agent\nmetadata:\n  name: step-a\n", encoding="utf-8")
    mas_path.write_text(
        "kind: mas\nmetadata:\n  name: demo\nspec:\n  workflow:\n    entry: step-a\n  agency:\n    agents:\n      - "
        "id: step-a\n        ref: agents/step-a.yaml\n",
        encoding="utf-8",
    )

    compose = ComposeResult(
        mas_id="demo",
        mas_config={
            "kind": "mas",
            "metadata": {"name": "demo"},
            "spec": {
                "workflow": {
                    "entry": "step-a",
                    "nodes": [{"id": "step-a"}, {"id": "step-b"}],
                },
                "agency": {"agents": [{"id": "step-a", "ref": "agents/step-a.yaml"}]},
            },
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[
                AgentBindSlice(agent_id="step-a", pattern_plugin_id="react@v1"),
                AgentBindSlice(agent_id="step-b", pattern_plugin_id="react@v1"),
            ],
        ),
        plan=PlacementPlan(),
    )

    prepared = SimpleNamespace(
        instance=SimpleNamespace(load_checkpoint=lambda *_: None),
        enriched_manifest={"metadata": {"name": "step-a"}, "spec": {}},
        manifest_path=tmp_path / "agents" / "step-a.yaml",
        session_id="sid-1",
    )
    stub_instance = SimpleNamespace(
        driver=SimpleNamespace(agent_id=None, observability=None),
        obs_plugin_set=None,
    )
    materialized = MaterializedMas(
        compose=compose,
        materialized=SimpleNamespace(instances={"step-a": stub_instance, "step-b": stub_instance}),
        mas_base_dir=mas_path.parent,
    )

    with patch("mas.ctl.benchmark.runner.compose_run", return_value=compose):
        with patch("mas.ctl.benchmark.runner.materialize_mas_compose", return_value=materialized):
            with patch("mas.ctl.benchmark.runner.prepare_delegation_entry_session", return_value=prepared):
                with patch("mas.ctl.benchmark.runner.wire_peer_delegation"):
                    with patch.object(
                        MasBenchRunner, "_run_controller_turns", return_value=RunResult(content="ok", status="ok")
                    ) as run_turns:
                        result = MasBenchRunner().run(
                            "hello",
                            config=compose.mas_config,
                            spec_path=mas_path,
                            output_dir=tmp_path / "out",
                        )

    assert result.status == "ok"
    run_turns.assert_called_once()


def test_compose_path_skips_stacked_merge_for_loaded_mas_raw(tmp_path: Path):
    from mas.lab.manifest.load import LOADED_MAS_RAW_KEY

    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        "kind: mas\nmetadata:\n  name: demo\nspec:\n  workflow:\n    entry: alpha\n",
        encoding="utf-8",
    )
    agent_path = tmp_path / "agents" / "alpha.yaml"
    agent_path.parent.mkdir()
    agent_path.write_text("kind: agent\nmetadata:\n  name: alpha\n", encoding="utf-8")

    loaded_raw = {
        LOADED_MAS_RAW_KEY: True,
        "mas": {"entry_agent": "alpha"},
        "agents": [{"id": "alpha", "_agent_dir": str(agent_path.parent), "pattern_framework": "cot"}],
    }
    compose = ComposeResult(
        mas_id="demo",
        mas_config={
            "kind": "mas",
            "metadata": {"name": "demo"},
            "spec": {
                "workflow": {"entry": "alpha"},
                "agency": {"agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}]},
            },
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="cot@v1")],
        ),
        plan=PlacementPlan(),
    )

    with patch("mas.ctl.benchmark.runner.compose_run", return_value=compose):
        with patch("mas.ctl.benchmark.runner.merge_stacked_entry_agent_manifest") as merge:
            with patch("mas.ctl.benchmark.runner.instantiate_runtime") as inst:
                inst.return_value = (object(), None)
                with patch.object(MasBenchRunner, "_run_controller_turns") as turns:
                    turns.return_value = RunResult(content="ok")
                    MasBenchRunner().run(
                        "hello",
                        config=loaded_raw,
                        spec_path=mas_path,
                        output_dir=tmp_path / "out",
                    )
                    merge.assert_not_called()


def test_entry_agent_from_compose_mas_kind_nonstandard_filename(tmp_path: Path):
    mas_path = tmp_path / "triage.yaml"
    mas_path.write_text(
        "kind: mas\nmetadata:\n  name: triage\nspec:\n  workflow:\n    entry: alpha\n  agency:\n    agents:\n      - "
        "id: alpha\n        ref: agents/alpha.yaml\n",
        encoding="utf-8",
    )
    agent_path = tmp_path / "agents" / "alpha.yaml"
    agent_path.parent.mkdir()
    agent_path.write_text("kind: agent\nmetadata:\n  name: alpha\n", encoding="utf-8")

    compose = ComposeResult(
        mas_id="triage",
        mas_config={
            "kind": "mas",
            "metadata": {"name": "triage"},
            "spec": {
                "workflow": {"entry": "alpha"},
                "agency": {"agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}]},
            },
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="triage",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )

    manifest, path = entry_agent_from_compose(compose, mas_path)
    assert path == agent_path
    assert manifest.get("metadata", {}).get("name") == "alpha"


def test_mas_bench_forwards_infra_refs_to_compose(tmp_path: Path):
    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        "kind: mas\nmetadata:\n  name: demo\nspec:\n  workflow:\n    entry: alpha\n"
        "  agency:\n    agents:\n      - id: alpha\n        ref: agents/alpha.yaml\n",
        encoding="utf-8",
    )
    agent_path = tmp_path / "agents" / "alpha.yaml"
    agent_path.parent.mkdir()
    agent_path.write_text("kind: agent\nmetadata:\n  name: alpha\n", encoding="utf-8")

    compose = ComposeResult(
        mas_id="demo",
        mas_config={
            "kind": "mas",
            "metadata": {"name": "demo"},
            "spec": {
                "workflow": {"entry": "alpha"},
                "agency": {"agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}]},
            },
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=["standard:llm-proxy"],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )

    with patch("mas.ctl.benchmark.runner.compose_run", return_value=compose) as compose_run:
        with patch("mas.ctl.benchmark.runner.instantiate_runtime") as inst:
            inst.return_value = (object(), None)
            with patch.object(MasBenchRunner, "_run_controller_turns") as turns:
                turns.return_value = RunResult(content="ok")
                MasBenchRunner().run(
                    "hello",
                    config=compose.mas_config,
                    spec_path=mas_path,
                    output_dir=tmp_path / "out",
                    infra_refs=["standard:llm-proxy"],
                )
    req = compose_run.call_args[0][0]
    assert req.infra_refs == ["standard:llm-proxy"]


def test_mas_bench_composes_library_app_with_experiment_workspace(tmp_path: Path):
    """An app from a library must use the experiment's workspace infra, not the library's."""
    library_app = tmp_path / "library" / "apps" / "demo"
    library_app.mkdir(parents=True)
    mas_path = library_app / "mas.yaml"
    mas_path.write_text("kind: mas\nmetadata:\n  name: demo\n", encoding="utf-8")
    experiment_dir = tmp_path / "experiment"
    experiment_dir.mkdir()
    compose = ComposeResult(
        mas_id="demo",
        mas_config={"kind": "mas", "metadata": {"name": "demo"}, "spec": {}},
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )

    with patch("mas.ctl.benchmark.runner.compose_run", return_value=compose) as compose_run:
        with patch("mas.ctl.benchmark.runner.entry_agent_from_compose", return_value=({}, mas_path)):
            with patch("mas.ctl.benchmark.runner.instantiate_runtime") as inst:
                inst.return_value = (object(), None)
                with patch("mas.ctl.benchmark.runner.WorkspaceConfig.load") as ws_load:
                    with patch.object(MasBenchRunner, "_run_controller_turns") as turns:
                        turns.return_value = RunResult(content="ok")
                        MasBenchRunner().run(
                            "hello",
                            config=compose.mas_config,
                            spec_path=mas_path,
                            output_dir=tmp_path / "out",
                            workspace_root=experiment_dir,
                        )
    assert compose_run.call_args[0][0].workspace_root == experiment_dir
    ws_load.assert_called_with(experiment_dir)


def test_attach_tool_fixtures_sets_every_agent_context():
    from mas.ctl.benchmark.runner import _attach_tool_fixtures

    agents = [SimpleNamespace(driver=SimpleNamespace(ctx=SimpleNamespace())) for _ in range(2)]
    fixtures = {"by_tool": {"*": {"services": {}}}}
    _attach_tool_fixtures(agents, fixtures, {"region": "eu-west-1"})
    assert all(a.driver.ctx.tool_fixtures is fixtures for a in agents)
    assert all(a.driver.ctx.runtime_params == {"region": "eu-west-1"} for a in agents)
