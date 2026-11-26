#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``count`` on a MAS agency entry expands into concrete agents, ids ``<id>-1`` .. ``<id>-n``."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from mas.ctl.compile import CompileError, compile_manifest
from mas.ctl.infra.identities import agency_ids, agency_row_id, workflow_peers
from mas.ctl.manifest.agency_groups import (
    MAX_COUNT,
    AgencyGroupError,
    declared_group_ids,
    expand_agency_groups,
    group_members,
)
from mas.ctl.overlay import merge_overlay, rekey_agent_patches
from mas.ctl.overlay.merge import OverlayTargetError
from mas.ctl.validate import validate_data, validate_file

AGENT = """apiVersion: mas/v1
kind: Agent
metadata:
  name: {name}
spec:
  description: {name} agent
  models:
    - model: gpt-4o-mini
"""

MODERATOR = """apiVersion: mas/v1
kind: Agent
metadata:
  name: moderator
spec:
  description: moderator agent
  models:
    - model: gpt-4o-mini
"""


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _mas(agents: list[dict], workflow: dict | None = None) -> dict:
    spec: dict = {"agency": {"agents": agents}}
    if workflow is not None:
        spec["workflow"] = workflow
    return {"apiVersion": "mas/v1", "kind": "MAS", "metadata": {"name": "grouped"}, "spec": spec}


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "moderator.yaml").write_text(MODERATOR, encoding="utf-8")
    (tmp_path / "agents" / "worker.yaml").write_text(AGENT.format(name="worker"), encoding="utf-8")
    return tmp_path


def _write_mas(folder: Path, doc: dict) -> Path:
    return _write(folder / "mas.yaml", doc)


WORKFLOW = {
    "entry": "moderator",
    "nodes": [
        {"id": "moderator", "delegates_to": ["worker"]},
    ],
}


def _grouped_mas(count: int = 3) -> dict:
    return _mas(
        [
            {"id": "moderator", "ref": "./agents/moderator.yaml"},
            {"id": "worker", "ref": "./agents/worker.yaml", "count": count},
        ],
        WORKFLOW,
    )


def test_count_expands_to_suffixed_ids_with_the_same_ref(folder: Path) -> None:
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(3)), fill_defaults=False)

    assert compiled.agent_ids == ["moderator", "worker-1", "worker-2", "worker-3"]
    rows = compiled.mas["spec"]["agency"]["agents"]
    assert [row["id"] for row in rows] == ["moderator", "worker-1", "worker-2", "worker-3"]
    assert all(row["ref"] == "./agents/worker.yaml" for row in rows[1:])
    assert all("count" not in row for row in rows)


def test_count_creates_the_group_and_each_copy_is_named_after_its_id(folder: Path) -> None:
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(3)), fill_defaults=False)

    for index in (1, 2, 3):
        assert compiled.agents[f"worker-{index}"]["metadata"]["name"] == f"worker-{index}"
    assert compiled.mas["spec"]["agency"]["groups"] == {"worker": ["worker-1", "worker-2", "worker-3"]}
    marked = {row["id"]: row.get("group") for row in compiled.mas["spec"]["agency"]["agents"]}
    assert marked == {"moderator": None, "worker-1": "worker", "worker-2": "worker", "worker-3": "worker"}


def test_copies_are_separate_documents_with_their_own_relpath(folder: Path) -> None:
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(2)), fill_defaults=False)

    assert compiled.agent_relpaths["worker-1"] == "agents/worker-1.yaml"
    assert compiled.agent_relpaths["worker-2"] == "agents/worker-2.yaml"
    compiled.agents["worker-1"]["spec"]["description"] = "changed"
    assert compiled.agents["worker-2"]["spec"]["description"] == "worker agent"


def test_group_id_in_delegates_to_expands_to_every_member(folder: Path) -> None:
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(3)), fill_defaults=False)

    assert workflow_peers(compiled.mas, "moderator") == ["worker-1", "worker-2", "worker-3"]


def test_group_node_becomes_one_node_per_member_with_the_same_settings(folder: Path) -> None:
    workflow = {
        "entry": "moderator",
        "nodes": [
            {"id": "moderator", "delegates_to": ["worker"]},
            {"id": "worker", "role": "specialist", "delegates_to": ["moderator"]},
        ],
    }
    compiled = compile_manifest(
        _write_mas(folder, _mas(_grouped_mas(2)["spec"]["agency"]["agents"], workflow)), fill_defaults=False
    )

    nodes = compiled.mas["spec"]["workflow"]["nodes"]
    assert [node["id"] for node in nodes] == ["moderator", "worker-1", "worker-2"]
    assert nodes[1] == {"id": "worker-1", "role": "specialist", "delegates_to": ["moderator"]}
    assert nodes[2] == {"id": "worker-2", "role": "specialist", "delegates_to": ["moderator"]}


def test_count_one_is_still_suffixed_and_references_follow(folder: Path) -> None:
    doc = _grouped_mas(1)
    doc["spec"]["workflow"] = {"entry": "worker", "nodes": [{"id": "worker"}]}
    compiled = compile_manifest(_write_mas(folder, doc), fill_defaults=False)

    assert compiled.agent_ids == ["moderator", "worker-1"]
    assert compiled.mas["spec"]["workflow"]["entry"] == "worker-1"
    assert [node["id"] for node in compiled.mas["spec"]["workflow"]["nodes"]] == ["worker-1"]


def test_entry_agent_cannot_be_a_group_with_more_than_one_copy(folder: Path) -> None:
    doc = _grouped_mas(3)
    doc["spec"]["workflow"] = {"entry": "worker", "nodes": []}
    with pytest.raises(CompileError, match="workflow.entry names count group 'worker' with 3 agents"):
        compile_manifest(_write_mas(folder, doc), fill_defaults=False, validate=False)
    assert not validate_data(doc, kind="mas").ok


def test_overlay_replace_can_set_the_count(folder: Path) -> None:
    mas_path = _write_mas(folder, _grouped_mas(3))
    overlay = _write(
        folder / "overlay.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "six-workers"},
            "spec": {
                "target": {"kind": "MAS"},
                "patch": {
                    "agents": {
                        "$op": {
                            "replace": [
                                {"id": "moderator", "ref": "./agents/moderator.yaml"},
                                {"id": "worker", "ref": "./agents/worker.yaml", "count": 6},
                            ]
                        }
                    }
                },
            },
        },
    )
    compiled = compile_manifest(mas_path, [overlay], fill_defaults=False)

    assert compiled.agent_ids == ["moderator"] + [f"worker-{i}" for i in range(1, 7)]
    assert workflow_peers(compiled.mas, "moderator") == [f"worker-{i}" for i in range(1, 7)]


def test_overlay_add_can_set_the_count(folder: Path) -> None:
    mas_path = _write_mas(folder, _mas([{"id": "moderator", "ref": "./agents/moderator.yaml"}]))
    overlay = _write(
        folder / "overlay.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "add-workers"},
            "spec": {
                "target": {"kind": "MAS"},
                "patch": {"agents": {"$op": {"add": [{"id": "worker", "ref": "./agents/worker.yaml", "count": 2}]}}},
            },
        },
    )
    compiled = compile_manifest(mas_path, [overlay], fill_defaults=False)

    assert compiled.agent_ids == ["moderator", "worker-1", "worker-2"]


def test_overlay_agent_patch_by_group_id_reaches_every_copy(folder: Path) -> None:
    overlay = _write(
        folder / "overlay.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "tune-workers"},
            "spec": {
                "target": {"kind": "MAS"},
                "patch": {"agents": {"worker": {"description": "tuned worker"}}},
            },
        },
    )
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(2)), [overlay], fill_defaults=False)

    assert compiled.agents["worker-1"]["spec"]["description"] == "tuned worker"
    assert compiled.agents["worker-2"]["spec"]["description"] == "tuned worker"
    assert compiled.agents["moderator"]["spec"]["description"] == "moderator agent"


def test_generated_id_colliding_with_an_explicit_agent_is_an_error(folder: Path) -> None:
    doc = _mas(
        [
            {"id": "worker", "ref": "./agents/worker.yaml", "count": 3},
            {"id": "worker-2", "ref": "./agents/worker.yaml"},
        ]
    )
    with pytest.raises(AgencyGroupError, match="'worker-2' is generated by count group 'worker'"):
        expand_agency_groups(doc)
    assert not validate_data(doc, kind="mas").ok


@pytest.mark.parametrize("count", [0, -1, "3", 2.0, True])
def test_count_must_be_an_integer_of_at_least_one(count: object) -> None:
    doc = _mas([{"id": "worker", "ref": "./agents/worker.yaml", "count": count}])
    with pytest.raises(AgencyGroupError, match="count"):
        expand_agency_groups(doc)
    assert not validate_data(doc, kind="mas").ok


def test_count_on_an_external_agent_without_ref_is_refused() -> None:
    doc = _mas([{"id": "remote", "count": 2}])
    with pytest.raises(AgencyGroupError, match="'remote' has count but no ref"):
        expand_agency_groups(doc)


def test_count_on_the_short_form_is_refused() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "x"},
        "spec": {"agents": [{"ref": "a.yaml", "count": 2}]},
    }
    with pytest.raises(AgencyGroupError, match="spec.agency.agents"):
        expand_agency_groups(doc)


def test_inline_definition_with_count_expands_from_the_same_definition() -> None:
    inline = {
        "kind": "Agent",
        "metadata": {"name": "scout"},
        "spec": {"description": "scout", "models": [{"model": "gpt-4o-mini"}]},
        "count": 2,
    }
    expanded = expand_agency_groups(_mas([inline]))

    rows = expanded["spec"]["agency"]["agents"]
    assert [row["metadata"]["name"] for row in rows] == ["scout-1", "scout-2"]
    assert [agency_row_id(row) for row in rows] == ["scout-1", "scout-2"]
    assert agency_ids(expanded) == ["scout-1", "scout-2"]
    assert all(row["spec"] == inline["spec"] for row in rows)
    assert group_members(expanded) == {"scout": ["scout-1", "scout-2"]}


def test_document_without_count_is_returned_unchanged() -> None:
    doc = _mas(
        [{"id": "moderator", "ref": "./agents/moderator.yaml"}, {"id": "worker", "ref": "./agents/worker.yaml"}],
        WORKFLOW,
    )
    assert expand_agency_groups(doc) is doc


def test_document_without_count_compiles_to_the_same_ids(folder: Path) -> None:
    workflow = {"entry": "moderator", "nodes": [{"id": "moderator", "delegates_to": ["worker"]}, {"id": "worker"}]}
    doc = _mas(
        [{"id": "moderator", "ref": "./agents/moderator.yaml"}, {"id": "worker", "ref": "./agents/worker.yaml"}],
        workflow,
    )
    compiled = compile_manifest(_write_mas(folder, doc), fill_defaults=False)

    assert compiled.agent_ids == ["moderator", "worker"]
    assert "groups" not in compiled.mas["spec"]["agency"]
    assert compiled.agents["worker"]["metadata"]["name"] == "worker"


def test_validate_accepts_a_valid_count(folder: Path) -> None:
    assert validate_data(_grouped_mas(3), kind="mas").ok


def test_count_three_runs_three_distinct_agents(folder: Path) -> None:
    from unittest.mock import patch

    from mas.ctl.compose.runner import ComposeRequest, compose_run
    from mas.ctl.executor.mas_session import materialize_mas_compose
    from mas.ctl.session.engine_factory import EngineSelection
    from mas.ctl.session.exposure import make_runtime_handler
    from mas.runtime.engine.simulated import SimulatedEngine

    def _selection(*_args, **_kwargs) -> EngineSelection:
        engine = SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="done")
        return EngineSelection(engine=engine, mode="injected")

    copies = ("worker-1", "worker-2", "worker-3")
    _write_mas(folder, _grouped_mas(3))
    with patch("mas.ctl.session.bootstrap.build_engine", side_effect=_selection):
        result = compose_run(
            ComposeRequest(manifest=folder / "mas.yaml", validate=False, infra_refs=["standard:openai"])
        )
        materialized = materialize_mas_compose(result, mas_base_dir=folder)
        instances = materialized.materialized.instances

        from mas.ctl.manifest.mas_agent_merge import enrich_entry_agent_for_delegation
        from mas.runtime.boundary.delegation.policy import communication_peers

        assert agency_ids(result.mas_config) == ["moderator", *copies]
        assert workflow_peers(result.mas_config, "moderator") == list(copies)
        assert [aid for aid in instances if aid.startswith("worker-")] == list(copies)
        assert [a.agent_id for a in result.bind.agents if a.agent_id.startswith("worker-")] == list(copies)
        assert len({id(instances[aid].driver.engine) for aid in copies}) == 3

        moderator = yaml.safe_load((folder / "agents" / "moderator.yaml").read_text(encoding="utf-8"))
        enriched = enrich_entry_agent_for_delegation(moderator, result.mas_config, agent_id="moderator")
        assert communication_peers(enriched) == [{"id": aid} for aid in copies]

        replies = {}
        for aid in copies:
            handler = make_runtime_handler(instances[aid])
            replies[aid] = handler("task", turn_id=f"{aid}-t1", session_id=f"{aid}-s")

    for aid in copies:
        assert replies[aid]["text"] == "done"
        assert replies[aid]["context_id"] == f"{aid}-s"


def _count_overlay(folder: Path, counts: object, name: str = "sweep") -> Path:
    return _write(
        folder / f"{name}.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": name},
            "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"$op": {"count": counts}}}},
        },
    )


def _plain_mas(folder: Path) -> Path:
    """Moderator and worker, no count anywhere."""
    workflow = {"entry": "moderator", "nodes": [{"id": "moderator", "delegates_to": ["worker"]}, {"id": "worker"}]}
    return _write_mas(
        folder,
        _mas(
            [{"id": "moderator", "ref": "./agents/moderator.yaml"}, {"id": "worker", "ref": "./agents/worker.yaml"}],
            workflow,
        ),
    )


def test_count_op_over_a_base_without_count_makes_a_group(folder: Path) -> None:
    before = compile_manifest(_plain_mas(folder), fill_defaults=False)
    after = compile_manifest(_plain_mas(folder), [_count_overlay(folder, {"worker": 2})], fill_defaults=False)

    assert before.agent_ids == ["moderator", "worker"]
    assert after.agent_ids == ["moderator", "worker-1", "worker-2"]
    assert workflow_peers(after.mas, "moderator") == ["worker-1", "worker-2"]


def test_count_op_over_a_base_with_count_overrides_it(folder: Path) -> None:
    base = _write_mas(folder, _grouped_mas(3))
    to_two = compile_manifest(base, [_count_overlay(folder, {"worker": 2}, "two")], fill_defaults=False)
    to_one = compile_manifest(base, [_count_overlay(folder, {"worker": 1}, "one")], fill_defaults=False)

    assert to_two.agent_ids == ["moderator", "worker-1", "worker-2"]
    assert to_one.agent_ids == ["moderator", "worker-1"]
    assert to_one.mas["spec"]["workflow"]["nodes"][0]["delegates_to"] == ["worker-1"]


def test_count_op_leaves_other_entries_unchanged(folder: Path) -> None:
    doc = _mas(
        [
            {"id": "moderator", "ref": "./agents/moderator.yaml"},
            {"id": "worker", "ref": "./agents/worker.yaml"},
            {"id": "scout", "ref": "./agents/worker.yaml", "count": 2},
        ],
        {"entry": "moderator", "nodes": [{"id": "moderator"}]},
    )
    base = copy.deepcopy(doc)
    compiled = compile_manifest(_write_mas(folder, doc), [_count_overlay(folder, {"worker": 2})], fill_defaults=False)

    rows = compiled.mas["spec"]["agency"]["agents"]
    assert [row["id"] for row in rows] == ["moderator", "worker-1", "worker-2", "scout-1", "scout-2"]
    assert rows[0] == base["spec"]["agency"]["agents"][0]
    assert all(row["ref"] == "./agents/worker.yaml" for row in rows[1:])
    assert compiled.agent_ids == ["moderator", "worker-1", "worker-2", "scout-1", "scout-2"]


def test_count_op_unknown_id_is_an_error_naming_the_id(folder: Path) -> None:
    with pytest.raises(OverlayTargetError, match="'nobody'"):
        compile_manifest(_plain_mas(folder), [_count_overlay(folder, {"nobody": 2})], fill_defaults=False)


def _count_overlay_doc(counts: object) -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "sweep"},
        "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"$op": {"count": counts}}}},
    }


@pytest.mark.parametrize("value", [0, -2, "2", 1.5, True, None, MAX_COUNT + 1])
def test_count_op_value_must_be_an_integer_in_range(value: object) -> None:
    base = _mas([{"id": "worker", "ref": "./agents/worker.yaml"}])
    with pytest.raises(OverlayTargetError, match="agency agent 'worker': count must be"):
        merge_overlay(base, _count_overlay_doc({"worker": value}))


@pytest.mark.parametrize("value", [0, "2"])
def test_count_op_bad_value_is_refused_by_validation_before_merge(folder: Path, value: object) -> None:
    assert not validate_file(_count_overlay(folder, {"worker": value}), kind="overlay").ok
    with pytest.raises(ValueError, match="validation failed"):
        compile_manifest(_plain_mas(folder), [_count_overlay(folder, {"worker": value})], fill_defaults=False)


def test_count_op_must_be_a_mapping() -> None:
    base = _mas([{"id": "worker", "ref": "./agents/worker.yaml"}])
    with pytest.raises(OverlayTargetError, match="must map agency ids to integers"):
        merge_overlay(base, _count_overlay_doc(["worker"]))


def test_count_op_overlay_passes_the_overlay_schema(folder: Path) -> None:
    assert validate_file(_count_overlay(folder, {"worker": 2}), kind="overlay").ok
    assert not validate_file(_count_overlay(folder, {"worker": 0}), kind="overlay").ok


def test_text_example_ids_before_and_after_count_op(folder: Path) -> None:
    """The example in the docs: a count op turns ``worker`` into ``worker-1`` and ``worker-2``."""
    before = compile_manifest(_plain_mas(folder), fill_defaults=False).agent_ids
    after = compile_manifest(_plain_mas(folder), [_count_overlay(folder, {"worker": 2})], fill_defaults=False).agent_ids
    assert before == ["moderator", "worker"]
    assert after == ["moderator", "worker-1", "worker-2"]


def test_count_op_through_compose_rewrites_peers(folder: Path) -> None:
    from unittest.mock import patch

    from mas.ctl.compose.runner import ComposeRequest, compose_run
    from mas.ctl.session.engine_factory import EngineSelection
    from mas.runtime.engine.simulated import SimulatedEngine

    def _selection(*_args, **_kwargs) -> EngineSelection:
        engine = SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="done")
        return EngineSelection(engine=engine, mode="injected")

    mas_path = _plain_mas(folder)
    overlay = _count_overlay(folder, {"worker": 2})
    with patch("mas.ctl.session.bootstrap.build_engine", side_effect=_selection):
        result = compose_run(
            ComposeRequest(
                manifest=mas_path,
                overlay_paths=[overlay],
                validate=False,
                infra_refs=["standard:openai"],
            )
        )

    assert agency_ids(result.mas_config) == ["moderator", "worker-1", "worker-2"]
    assert workflow_peers(result.mas_config, "moderator") == ["worker-1", "worker-2"]
    assert [a.agent_id for a in result.bind.agents] == ["moderator", "worker-1", "worker-2"]


def test_inline_count_compose_ids_match_workflow(folder: Path) -> None:
    from unittest.mock import patch

    from mas.ctl.compose.runner import ComposeRequest, compose_run
    from mas.ctl.session.engine_factory import EngineSelection
    from mas.runtime.engine.simulated import SimulatedEngine

    def _selection(*_args, **_kwargs) -> EngineSelection:
        engine = SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="done")
        return EngineSelection(engine=engine, mode="injected")

    inline = {
        "kind": "Agent",
        "metadata": {"name": "scout"},
        "spec": {"description": "scout", "models": [{"model": "gpt-4o-mini"}]},
        "count": 2,
    }
    doc = _mas(
        [
            {"id": "moderator", "ref": "./agents/moderator.yaml"},
            inline,
        ],
        {"entry": "moderator", "nodes": [{"id": "moderator", "delegates_to": ["scout"]}]},
    )
    _write_mas(folder, doc)
    with patch("mas.ctl.session.bootstrap.build_engine", side_effect=_selection):
        result = compose_run(
            ComposeRequest(manifest=folder / "mas.yaml", validate=False, infra_refs=["standard:openai"])
        )

    assert agency_ids(result.mas_config) == ["moderator", "scout-1", "scout-2"]
    assert workflow_peers(result.mas_config, "moderator") == ["scout-1", "scout-2"]
    assert [a.agent_id for a in result.bind.agents] == ["moderator", "scout-1", "scout-2"]
    ids = {agency_row_id(row) for row in result.mas_config["spec"]["agency"]["agents"]}
    assert ids == {"moderator", "scout-1", "scout-2"}


def test_inline_all_overlay_reaches_every_copy(folder: Path) -> None:
    inline = {
        "kind": "Agent",
        "metadata": {"name": "scout"},
        "spec": {"description": "scout", "models": [{"model": "gpt-4o-mini"}]},
        "count": 2,
    }
    mas_path = _write_mas(
        folder,
        _mas(
            [{"id": "moderator", "ref": "./agents/moderator.yaml"}, inline],
            {"entry": "moderator", "nodes": [{"id": "moderator"}]},
        ),
    )
    overlay = _write(
        folder / "overlay.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "tune"},
            "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"$all": {"description": "tuned"}}}},
        },
    )
    compiled = compile_manifest(mas_path, [overlay], fill_defaults=False)

    assert compiled.agents["scout-1"]["spec"]["description"] == "tuned"
    assert compiled.agents["scout-2"]["spec"]["description"] == "tuned"
    assert compiled.agents["moderator"]["spec"]["description"] == "tuned"


def test_node_agent_count_one_rewrites_to_the_copy(folder: Path) -> None:
    workflow = {
        "entry": "moderator",
        "nodes": [
            {"id": "moderator", "delegates_to": ["worker"]},
            {"id": "team", "agent": "worker", "role": "specialist"},
        ],
    }
    compiled = compile_manifest(
        _write_mas(folder, _mas(_grouped_mas(1)["spec"]["agency"]["agents"], workflow)), fill_defaults=False
    )

    nodes = compiled.mas["spec"]["workflow"]["nodes"]
    assert nodes[1] == {"id": "team", "agent": "worker-1", "role": "specialist"}
    assert workflow_peers(compiled.mas, "worker-1") == []


def test_node_agent_cannot_be_a_group_with_more_than_one_copy(folder: Path) -> None:
    workflow = {
        "entry": "moderator",
        "nodes": [
            {"id": "moderator"},
            {"id": "team", "agent": "worker"},
        ],
    }
    doc = _mas(_grouped_mas(2)["spec"]["agency"]["agents"], workflow)
    with pytest.raises(CompileError, match="workflow node 'team' agent names count group 'worker'"):
        compile_manifest(_write_mas(folder, doc), fill_defaults=False, validate=False)


def test_control_group_id_matches_every_copy(folder: Path) -> None:
    from mas.ctl.session.flavour_control.plugins import control_lists_for_agent, matching_controls

    compiled = compile_manifest(_write_mas(folder, _grouped_mas(2)), fill_defaults=False)
    groups = group_members(compiled.mas)
    row = {"agents": "worker", "spec": {"governance": ["sample_governance"]}}

    assert matching_controls([row], agent_id="worker-1", groups=groups)
    assert matching_controls([row], agent_id="worker-2", groups=groups)
    assert matching_controls([row], agent_id="moderator", groups=groups) == []
    path_row = {"agents": ".mas.agent[worker]", "spec": {"governance": ["sample_governance"]}}
    assert matching_controls([path_row], agent_id="worker-1", groups=groups)
    assert matching_controls([path_row], agent_id="moderator", groups=groups) == []
    assert control_lists_for_agent([row], agent_id="worker-1", groups=groups)["governance"] == ["sample_governance"]
    assert control_lists_for_agent([row], agent_id="moderator", groups=groups) == {}


def test_bind_agents_group_id_rekeys_to_copies() -> None:
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.infra.bind import BindError, _flavour_with_group_bind, bind_session

    ids = ["moderator", "worker-1", "worker-2"]
    groups = {"worker": ["worker-1", "worker-2"]}
    flavour = {"bind": {"agents": {"worker": {"usage": ["listen"]}}}}

    with pytest.raises(BindError, match="names 'worker'"):
        bind_session(ResolvedInfra(), flavour, agent_ids=ids)
    with pytest.raises(BindError, match="bind.agents.worker-1"):
        bind_session(ResolvedInfra(), flavour, agent_ids=ids, groups=groups)

    rekeyed = _flavour_with_group_bind(
        {"bind": {"agents": {"worker": {"usage": ["listen"]}, "worker-1": {"usage": ["connect"]}}}},
        groups,
    )
    assert rekeyed["bind"]["agents"]["worker-1"] == {"usage": ["connect"]}
    assert rekeyed["bind"]["agents"]["worker-2"] == {"usage": ["listen"]}
    assert "worker" not in rekeyed["bind"]["agents"]


def _explicit_mas(groups: dict, workflow: dict | None = None) -> dict:
    rows = [
        {"id": "moderator", "ref": "./agents/moderator.yaml"},
        {"id": "critic", "ref": "./agents/worker.yaml"},
        {"id": "verifier", "ref": "./agents/worker.yaml"},
        {"id": "worker", "ref": "./agents/worker.yaml", "count": 2},
    ]
    doc = _mas(rows, workflow)
    doc["spec"]["agency"]["groups"] = groups
    return doc


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        ({"r": ["ghost"]}, "not in the agency"),
        ({"r": []}, "non-empty list"),
        ({"r": "critic"}, "non-empty list"),
        ({"r": ["critic"], "s": ["r"]}, "do not nest"),
        ({"critic": ["verifier"]}, "both a group and a declared agent"),
        ({"worker": ["critic"]}, "declared twice"),
        ({"r": ["worker-3"]}, "not in the agency"),
    ],
)
def test_an_invalid_explicit_group_is_an_error(groups: dict, message: str) -> None:
    doc = _explicit_mas(groups)
    with pytest.raises(AgencyGroupError, match=message):
        expand_agency_groups(doc)
    assert not validate_data(doc, kind="mas").ok


def test_explicit_groups_expand_and_mix_with_count_groups() -> None:
    doc = _explicit_mas(
        {"reviewers": ["critic", "verifier"], "everyone": ["critic", "worker"]},
        {"entry": "moderator", "nodes": [{"id": "moderator", "delegates_to": ["reviewers", "everyone"]}]},
    )
    expanded = expand_agency_groups(doc)
    assert group_members(expanded) == {
        "worker": ["worker-1", "worker-2"],
        "reviewers": ["critic", "verifier"],
        "everyone": ["critic", "worker-1", "worker-2"],
    }
    assert expanded["spec"]["workflow"]["nodes"][0]["delegates_to"] == ["critic", "verifier", "worker-1", "worker-2"]
    assert group_members(doc) == group_members(expanded)
    assert expand_agency_groups(expanded) == expanded


def test_explicit_group_members_keep_their_own_names() -> None:
    from mas.ctl.manifest.agency_groups import name_group_member

    expanded = expand_agency_groups(_explicit_mas({"reviewers": ["critic", "verifier"]}))
    manifest = {"metadata": {"name": "critic-original"}}
    assert name_group_member(manifest, agent_id="critic", mas_config=expanded) is manifest
    assert name_group_member(manifest, agent_id="worker-1", mas_config=expanded)["metadata"]["name"] == "worker-1"


def test_count_cap_is_enforced_with_a_clear_error() -> None:
    doc = _mas([{"id": "worker", "ref": "./agents/worker.yaml", "count": MAX_COUNT + 1}])
    with pytest.raises(AgencyGroupError, match=f"between 1 and {MAX_COUNT}"):
        expand_agency_groups(doc)
    assert not validate_data(doc, kind="mas").ok
    assert len(expand_agency_groups(_grouped_mas(MAX_COUNT))["spec"]["agency"]["agents"]) == MAX_COUNT + 1


def test_group_node_next_to_an_explicit_member_node_is_a_collision() -> None:
    doc = _mas(
        [
            {"id": "moderator", "ref": "./agents/moderator.yaml"},
            {"id": "worker", "ref": "./agents/worker.yaml", "count": 2},
        ],
        {
            "entry": "moderator",
            "nodes": [{"id": "moderator", "delegates_to": ["worker"]}, {"id": "worker"}, {"id": "worker-1"}],
        },
    )
    with pytest.raises(AgencyGroupError, match=r"workflow node ids \['worker-1'\] appear more than once"):
        expand_agency_groups(doc)


def test_inline_row_is_named_by_metadata_name_before_id() -> None:
    inline = {"kind": "Agent", "id": "alias", "metadata": {"name": "scout"}, "spec": {}}
    assert agency_row_id(inline) == "scout"
    assert agency_row_id({"id": "a", "name": "b"}) == "a"
    assert agency_row_id({"name": "b"}) == "b"
    expanded = expand_agency_groups(_mas([{**inline, "count": 2}]))
    assert agency_ids(expanded) == ["scout-1", "scout-2"]
    assert [row["id"] for row in expanded["spec"]["agency"]["agents"]] == ["scout-1", "scout-2"]


def test_inline_copy_with_a_name_key_is_renamed_too() -> None:
    inline = {"kind": "Agent", "name": "scout", "metadata": {"name": "scout"}, "spec": {}, "count": 2}
    assert agency_ids(expand_agency_groups(_mas([inline]))) == ["scout-1", "scout-2"]


def test_validate_knows_a_group_declared_with_name() -> None:
    doc = _mas(
        [
            {"id": "moderator", "ref": "./agents/moderator.yaml"},
            {"name": "worker", "ref": "./agents/worker.yaml", "count": 2},
        ],
        {"entry": "moderator", "nodes": [{"id": "moderator", "delegates_to": ["worker"]}]},
    )
    assert declared_group_ids(doc["spec"]) == {"worker"}
    assert not [i for i in validate_data(doc, kind="mas").issues if "unknown node id" in i.message]


def test_a_members_own_patch_wins_over_the_group_patch_in_any_key_order() -> None:
    mas = expand_agency_groups(_grouped_mas(2))
    group_first = rekey_agent_patches({"worker": {"description": "group"}, "worker-2": {"description": "copy"}}, mas)
    copy_first = rekey_agent_patches({"worker-2": {"description": "copy"}, "worker": {"description": "group"}}, mas)
    for patches in (group_first, copy_first):
        assert patches["worker-1"] == {"description": "group"}
        assert patches["worker-2"] == {"description": "copy"}


def test_the_patch_copies_do_not_share_state() -> None:
    mas = expand_agency_groups(_grouped_mas(2))
    patches = rekey_agent_patches({"worker": {"tools": ["a"]}}, mas)
    patches["worker-1"]["tools"].append("b")
    assert patches["worker-2"]["tools"] == ["a"]


def test_count_op_on_an_expanded_manifest_says_to_set_it_on_the_authored_one() -> None:
    expanded = expand_agency_groups(_grouped_mas(2))
    with pytest.raises(OverlayTargetError, match="set the count on the authored manifest"):
        merge_overlay(expanded, _count_overlay_doc({"worker": 3}))


def test_well_formed_compiled_copies_expand_as_a_no_op() -> None:
    expanded = expand_agency_groups(_grouped_mas(2))
    again = expand_agency_groups(expanded)

    assert again is expanded
    assert group_members(expanded) == {"worker": ["worker-1", "worker-2"]}


def test_overlay_patch_names_the_group_not_a_copy(folder: Path) -> None:
    copy_overlay = _write(
        folder / "copy.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "one"},
            "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"worker-1": {"description": "first"}}}},
        },
    )
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(2)), [copy_overlay], fill_defaults=False)
    assert compiled.agents["worker-1"]["spec"]["description"] == "worker agent"
    group_overlay = _write(
        folder / "group.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "all"},
            "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"worker": {"description": "tuned"}}}},
        },
    )
    compiled = compile_manifest(_write_mas(folder, _grouped_mas(2)), [group_overlay], fill_defaults=False)
    assert compiled.agents["worker-1"]["spec"]["description"] == "tuned"
    assert compiled.agents["worker-2"]["spec"]["description"] == "tuned"


def test_session_inspect_expands_count(folder: Path) -> None:
    from mas.ctl.cli.commands.hardening import _agent_specs
    from mas.ctl.infra.show import document_agent_ids
    from mas.ctl.manifest.agency_groups import loaded_session_mas

    authored = _grouped_mas(2)
    assert document_agent_ids(authored) == ["moderator", "worker"]
    session = loaded_session_mas(authored)
    assert document_agent_ids(session) == ["moderator", "worker-1", "worker-2"]
    path = _write_mas(folder, authored)
    assert set(_agent_specs(session, path)) == {"moderator", "worker-1", "worker-2"}


def test_hitl_and_obs_group_selector_matches_a_copy() -> None:
    from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
    from mas.ctl.session.manifest_config import observability_config_from_manifest

    flavour = {
        "_controls": [
            {
                "agents": "worker",
                "spec": {
                    "governance": [{"sample_governance": {"hitl_on_tool": True, "hitl_mode": "auto_approve"}}],
                    "observability": ["otel"],
                },
            }
        ]
    }
    groups = {"worker": ["worker-1", "worker-2"]}
    agent = {"apiVersion": "mas/v1", "kind": "Agent", "metadata": {"name": "worker-1"}, "spec": {}}
    assert resolve_hitl_from_manifest(agent, flavour_spec=flavour, agent_id="worker-1")[0] is None
    responder, _ = resolve_hitl_from_manifest(agent, flavour_spec=flavour, agent_id="worker-1", groups=groups)
    assert responder is not None
    skipped = observability_config_from_manifest({"spec": {}}, agent_id="worker-1", flavour_spec=flavour)
    attached = observability_config_from_manifest(
        {"spec": {}}, agent_id="worker-1", flavour_spec=flavour, groups=groups
    )
    assert skipped.plugins == ["native"]
    assert attached.plugins == ["otel"]


def test_bench_obs_config_group_selector_matches_a_copy(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import bench_obs_config

    flavour = {"_controls": [{"agents": "worker", "spec": {"observability": ["otel"]}}]}
    agent = {"apiVersion": "mas/v1", "kind": "Agent", "metadata": {"name": "worker-1"}, "spec": {}}
    skipped = bench_obs_config(tmp_path / "a", agent, tmp_path / "a.yaml", flavour=flavour)
    attached = bench_obs_config(
        tmp_path / "b", agent, tmp_path / "b.yaml", mas_config=expand_agency_groups(_grouped_mas(2)), flavour=flavour
    )
    assert "otel" not in skipped[1].plugins
    assert "otel" in attached[1].plugins


def test_group_members_reads_the_groups() -> None:
    expanded = expand_agency_groups(_grouped_mas(2))
    assert group_members(expanded) == {"worker": ["worker-1", "worker-2"]}
    assert group_members(_mas([{"id": "worker", "ref": "./agents/worker.yaml"}])) == {}
    assert group_members(None) == {}


def _overlay_remove(*ids: str) -> dict:
    return {"kind": "Overlay", "spec": {"target": {"kind": "MAS"}, "patch": {"agents": {"$op": {"remove": list(ids)}}}}}


def test_overlay_remove_of_a_member_leaves_its_groups() -> None:
    from mas.ctl.overlay.merge import merge_mas_overlay

    merged = merge_mas_overlay(_explicit_mas({"reviewers": ["critic", "verifier"]}), _overlay_remove("critic"))

    assert group_members(expand_agency_groups(merged))["reviewers"] == ["verifier"]


def test_overlay_remove_of_a_group_id_removes_its_members_and_the_group() -> None:
    from mas.ctl.overlay.merge import merge_mas_overlay

    doc = _explicit_mas({"reviewers": ["critic", "verifier"], "everyone": ["critic", "worker"]})
    merged = merge_mas_overlay(doc, _overlay_remove("reviewers"))

    assert agency_ids(expand_agency_groups(merged)) == ["moderator", "worker-1", "worker-2"]
    assert group_members(expand_agency_groups(merged))["everyone"] == ["worker-1", "worker-2"]
    assert "reviewers" not in group_members(expand_agency_groups(merged))


def test_overlay_remove_of_the_last_member_drops_the_group() -> None:
    from mas.ctl.overlay.merge import merge_mas_overlay

    merged = merge_mas_overlay(_explicit_mas({"solo": ["critic"]}), _overlay_remove("critic"))

    assert "solo" not in group_members(expand_agency_groups(merged))


def test_groups_without_an_agency_agents_list_are_an_error() -> None:
    doc = {"kind": "MAS", "spec": {"agency": {"groups": {"g": ["a"]}}, "agents": [{"id": "a"}]}}
    with pytest.raises(AgencyGroupError, match="needs a spec.agency.agents list"):
        expand_agency_groups(doc)


def test_explicit_group_members_keep_their_agent_file_in_a_compile(folder: Path) -> None:
    doc = _explicit_mas({"reviewers": ["critic", "verifier"]})
    doc["spec"]["workflow"] = {"entry": "moderator", "nodes": [{"id": "moderator", "delegates_to": ["reviewers"]}]}
    compiled = compile_manifest(_write_mas(folder, doc), fill_defaults=False)

    assert compiled.agent_relpaths["critic"] == "./agents/worker.yaml"
    assert compiled.agent_relpaths["worker-1"] == "agents/worker-1.yaml"


def test_bind_agents_two_groups_that_disagree_on_a_member_is_an_error() -> None:
    from mas.ctl.infra.bind import BindError, _flavour_with_group_bind

    groups = {"a": ["x", "y"], "b": ["y", "z"]}
    with pytest.raises(BindError, match="both set 'y'"):
        _flavour_with_group_bind(
            {"bind": {"agents": {"a": {"usage": ["listen"]}, "b": {"usage": ["connect"]}}}}, groups
        )
    same = _flavour_with_group_bind(
        {"bind": {"agents": {"a": {"usage": ["listen"]}, "b": {"usage": ["listen"]}}}}, groups
    )
    assert sorted(same["bind"]["agents"]) == ["x", "y", "z"]
