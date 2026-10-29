#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""MAS → entry agent manifest merge."""

from pathlib import Path

import yaml
from mas.ctl.manifest.mas_agent_merge import enrich_entry_agent_for_delegation, wire_entry_engine_delegation
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.tools import openai_tools, resolve_manifest_tool_refs


class _StubComm:
    def send(self, *args: object, **kwargs: object) -> str:
        return "ok"


def test_enrich_entry_agent_injects_workflow(tmp_path: Path):
    peer_yaml = tmp_path / "agents" / "alpha.yaml"
    peer_yaml.parent.mkdir(parents=True)
    peer_yaml.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "alpha"},
                "spec": {"description": "Alpha specialist for numeric baselines."},
            }
        ),
        encoding="utf-8",
    )
    agent = {
        "metadata": {"name": "entry"},
        "spec": {"description": "Entry orchestrator."},
    }
    mas = {
        "spec": {
            "agents": [{"id": "alpha", "ref": "agents/alpha.yaml"}],
            "workflow": {
                "entry": "entry",
                "nodes": [{"id": "entry", "delegates_to": ["alpha", "beta"]}],
            },
        }
    }
    enriched = enrich_entry_agent_for_delegation(
        agent,
        mas,
        mas_base_dir=tmp_path,
    )
    assert enriched["spec"]["workflow"]["entry"] == "entry"
    assert "delegation_peer_descriptions" not in enriched["spec"]
    engine = LiveLlmEngine(manifest=enriched, use_tool_loop=True)
    wire_entry_engine_delegation(
        engine,
        enriched,
        tmp_path,
        comm=_StubComm(),
        entry_agent_id="entry",
        mas_config=mas,
        mas_base_dir=tmp_path,
    )
    tools = openai_tools(enriched, agent_id="entry")
    by_name_generic = {t["function"]["name"]: t for t in tools}
    assert by_name_generic["delegate_to_alpha"]["function"]["description"].startswith(
        "Delegate a sub-task to agent alpha."
    )
    tools = openai_tools(
        enriched,
        agent_id="entry",
        peer_descriptions=engine.delegation_peer_descriptions,
    )
    by_name = {t["function"]["name"]: t for t in tools}
    assert "delegate_to_alpha" in by_name
    assert "Alpha specialist" in by_name["delegate_to_alpha"]["function"]["description"]
    assert by_name["delegate_to_beta"]["function"]["description"] == "Delegate a sub-task to agent beta."


def test_resolve_tool_refs_from_yaml(tmp_path: Path):
    tool_yaml = tmp_path / "run_action.tool.yaml"
    tool_yaml.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "run_action"},
                "spec": {"description": "act"},
            }
        ),
        encoding="utf-8",
    )
    agent = {"spec": {"tools": [{"ref": "run_action.tool.yaml"}]}}
    resolved = resolve_manifest_tool_refs(agent, tmp_path)
    assert resolved["spec"]["tools"][0]["name"] == "run_action"


def test_resolve_tool_refs_rejects_path_outside_base_dir(tmp_path: Path):
    outside = tmp_path.parent / "outside.tool.yaml"
    outside.write_text(
        yaml.safe_dump({"metadata": {"name": "evil"}, "spec": {}}),
        encoding="utf-8",
    )
    agent = {"spec": {"tools": [{"ref": f"../{outside.name}"}]}}
    resolved = resolve_manifest_tool_refs(agent, tmp_path)
    item = resolved["spec"]["tools"][0]
    assert "name" not in item


def test_resolve_tool_refs_does_not_alias_original_manifest(tmp_path: Path):
    tool_yaml = tmp_path / "run_action.tool.yaml"
    tool_yaml.write_text(
        yaml.safe_dump({"metadata": {"name": "run_action"}, "spec": {"description": "act"}}),
        encoding="utf-8",
    )
    agent = {"spec": {"tools": [{"ref": "run_action.tool.yaml"}]}}
    resolved = resolve_manifest_tool_refs(agent, tmp_path)
    resolved["spec"]["tools"][0]["name"] = "mutated"
    assert agent["spec"]["tools"][0].get("name") != "mutated"


def test_wire_entry_engine_delegation_skips_when_no_peers():
    class _Engine:
        manifest = None
        delegation = "unset"

    engine = _Engine()
    manifest = {
        "metadata": {"name": "solo"},
        "spec": {"workflow": {"entry": "solo", "nodes": [{"id": "solo"}]}},
    }
    wire_entry_engine_delegation(
        engine,
        manifest,
        Path("."),
        comm=_StubComm(),
        entry_agent_id="solo",
    )
    assert engine.delegation is None


def test_wire_entry_engine_delegation_skips_when_delegates_to_empty():
    class _Engine:
        manifest = None
        delegation = "unset"

    engine = _Engine()
    manifest = {
        "metadata": {"name": "leaf"},
        "spec": {
            "workflow": {
                "entry": "leaf",
                "nodes": [
                    {"id": "leaf", "delegates_to": []},
                    {"id": "other", "delegates_to": ["worker"]},
                ],
            }
        },
    }
    wire_entry_engine_delegation(
        engine,
        manifest,
        Path("."),
        comm=_StubComm(),
        entry_agent_id="leaf",
    )
    assert engine.delegation is None


def test_wire_entry_engine_delegation_enables_tool_loop_on_leaf():
    from mas.runtime.engine.llm_live import LiveLlmEngine

    engine = LiveLlmEngine(manifest=None, use_tool_loop=False, use_cache=False)
    engine.install_infra_pipeline(
        [{"middleware": "llm_cache", "params": {"allow_read": False, "allow_write": False}}],
    )

    manifest = {
        "metadata": {"name": "entry"},
        "spec": {
            "workflow": {
                "entry": "entry",
                "nodes": [{"id": "entry", "delegates_to": ["peer"]}],
            },
        },
    }
    wire_entry_engine_delegation(
        engine,
        manifest,
        Path("."),
        comm=_StubComm(),
        entry_agent_id="entry",
    )
    assert engine.use_tool_loop is True
    assert engine.delegation is not None
    assert engine.manifest is manifest
    assert engine.delegation._allowed == frozenset({"peer"})


def test_reset_engine_delegation_does_not_suppress_repeated_delegation():
    from mas.ctl.manifest.mas_agent_merge import reset_engine_delegation
    from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
    from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator

    calls: list[str] = []

    def run_turn(agent_id: str, task: str, correlation_id: int, caller_call_id: str, context_id: str) -> str:
        calls.append(agent_id)
        return f"findings:{agent_id}:{task}"

    class _Engine:
        def __init__(self) -> None:
            self.delegation = LlmDelegator(
                comm=LocalAgentComm(run_turn), allowed_peers=["peer"]
            )

    engine = _Engine()
    assert engine.delegation.delegate("peer", "task") == "findings:peer:task"
    reset_engine_delegation(engine)
    assert engine.delegation.delegate("peer", "task") == "findings:peer:task"
    assert calls == ["peer", "peer"]


def test_reset_engine_delegation_walks_inner_wrapper():
    from mas.ctl.manifest.mas_agent_merge import reset_engine_delegation
    from mas.library.standard.plugins.agentcomm.local import LocalAgentComm
    from mas.library.standard.plugins.delegation.llm_delegator import LlmDelegator

    calls: list[str] = []

    def run_turn(agent_id: str, task: str, correlation_id: int, caller_call_id: str, context_id: str) -> str:
        calls.append(agent_id)
        return f"findings:{agent_id}:{task}"

    class _Engine:
        def __init__(self) -> None:
            self.delegation = LlmDelegator(
                comm=LocalAgentComm(run_turn), allowed_peers=["peer"]
            )

    class _Wrapper:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    inner = _Engine()
    engine = _Wrapper(inner)
    assert inner.delegation.delegate("peer", "task") == "findings:peer:task"
    reset_engine_delegation(engine)
    assert inner.delegation.delegate("peer", "task") == "findings:peer:task"
    assert calls == ["peer", "peer"]


def test_reset_engine_delegation_does_not_follow_mock_inner():
    from unittest.mock import MagicMock

    from mas.ctl.manifest.mas_agent_merge import reset_engine_delegation

    engine = MagicMock()
    reset_engine_delegation(engine)
    assert "inner" not in engine._mock_children
    assert "delegation" not in engine._mock_children


def test_apply_agency_entry_overlay_merges_context_and_tools():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "moderator"},
        "spec": {"context": {"role": "base prompt"}},
    }
    entry = {
        "id": "moderator",
        "spec": {"context": {"role": "overlay prompt"}},
        "tools": [{"ref": "tools/memory-search.tool.yaml"}],
    }
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["context"]["role"] == "overlay prompt"
    assert merged["spec"]["tools"] == [{"ref": "tools/memory-search.tool.yaml"}]


def test_apply_agency_entry_overlay_tools_op_remove_strips_from_base_list():
    """tools_remove (a separate, redundant field) was removed as a concept
    here too -- agency-entry inline tool removal now goes through the same
    tools: {"$op": {"remove": [...]}} sugar the overlay-patch level already
    supports (see docs/manifests/overlay.md), not a second attribute name
    for the same operation."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "a"},
        "spec": {"tools": ["calc", "web-search"]},
    }
    entry = {"id": "a", "tools": {"$op": {"remove": ["calc"]}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["tools"] == ["web-search"]


def test_apply_agency_entry_overlay_tools_op_remove_by_ref():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "b"},
        "spec": {"tools": [{"ref": "samples:tools/calc.tool.yaml"}, "web-search"]},
    }
    entry = {"id": "b", "tools": {"$op": {"remove": ["samples:tools/calc.tool.yaml"]}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["tools"] == ["web-search"]


def test_apply_agency_entry_overlay_tools_op_add_still_dedups():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "c"}, "spec": {"tools": ["calc"]}}
    entry = {"id": "c", "tools": {"$op": {"add": ["calc", "web-search"]}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["tools"] == ["calc", "web-search"]


def test_apply_agency_entry_overlay_skills_op_add_keeps_agent_skills():
    """An agency entry adding a skill must not drop the ones the agent
    manifest already declares. ``skills`` used to bypass the list_ops merge
    and assign straight onto the spec, so ``{"$op": {"add": [...]}}`` silently
    replaced the agent's own list -- the skill was gone in that arm only."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "d"}, "spec": {"skills": ["settlement-protocol"]}}
    entry = {"id": "d", "skills": {"$op": {"add": ["l9-accord-v2-receiver"]}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["skills"] == ["settlement-protocol", "l9-accord-v2-receiver"]


def test_apply_agency_entry_overlay_skills_op_add_dedups():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "e"}, "spec": {"skills": ["a"]}}
    entry = {"id": "e", "skills": {"$op": {"add": ["a", "b"]}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["skills"] == ["a", "b"]


def test_apply_agency_entry_overlay_skills_op_remove_and_clear():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "f"}, "spec": {"skills": ["a", "b"]}}
    assert apply_agency_entry_overlay(manifest, {"id": "f", "skills": {"$op": {"remove": ["a"]}}})["spec"][
        "skills"
    ] == ["b"]
    assert (
        apply_agency_entry_overlay(manifest, {"id": "f", "skills": {"$op": {"clear": True}}})["spec"]["skills"] == []
    )


def test_apply_agency_entry_overlay_skills_plain_list_still_replaces():
    """Unchanged ergonomics: a raw list is an explicit replace, matching the
    ``list_ops`` strategy declared for ``spec.skills`` in agent.schema.yaml."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "g"}, "spec": {"skills": ["a", "b"]}}
    entry = {"id": "g", "skills": ["c"]}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["skills"] == ["c"]


def test_apply_agency_entry_overlay_skills_empty_list_clears():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "h"}, "spec": {"skills": ["a"]}}
    merged = apply_agency_entry_overlay(manifest, {"id": "h", "skills": []})
    assert merged["spec"]["skills"] == []


def test_base_mas_rejects_skills_on_agency_row():
    """Skills belong to the agent manifest. An agency row only carries them
    when an overlay patches that agent, never in a hand-written MAS."""
    from mas.ctl.validate.validator import validate_data

    mas = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "m"},
        "spec": {
            "agency": {"agents": [{"id": "a", "ref": "agents/a.yaml", "skills": ["s"]}]},
            "workflow": {"entry": "a", "nodes": [{"id": "a"}]},
        },
    }
    result = validate_data(mas, resolve_refs=False)
    assert not result.ok
    assert any("spec.agency.agents.0" in str(issue) for issue in result.issues)


def test_stacked_mas_overlays_add_skills_on_top_of_agent_manifest():
    """End-to-end path: two MAS overlays add skills through $entry/$not-entry,
    then the referenced agent YAML is loaded. The agent's own skills survive.
    Form A rows stay {id, ref} — the patch is not parked on the MAS."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay
    from mas.ctl.overlay.merge import accumulate_agent_patches, loaded_agent_patches, merge_mas_overlay
    from mas.ctl.validate.validator import validate_data

    mas = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "lead", "ref": "agents/lead.yaml"},
                    {"id": "role", "ref": "agents/role.yaml"},
                ]
            },
            "workflow": {"entry": "lead", "nodes": [{"id": "lead"}, {"id": "role"}]},
        },
    }

    def overlay(entry_skill: str, other_skill: str) -> dict:
        return {
            "spec": {
                "target": {"kind": "MAS"},
                "patch": {
                    "agents": {
                        "$entry": {"skills": {"$op": {"add": [entry_skill]}}},
                        "$not-entry": {"skills": {"$op": {"add": [other_skill]}}},
                    }
                },
            }
        }

    patches: dict = {}
    first = overlay("orch-a", "recv-a")
    mas = merge_mas_overlay(mas, first)
    patches = accumulate_agent_patches(patches, loaded_agent_patches(first, mas))
    second = overlay("orch-b", "recv-b")
    mas = merge_mas_overlay(mas, second)
    patches = accumulate_agent_patches(patches, loaded_agent_patches(second, mas))
    rows = {row["id"]: row for row in mas["spec"]["agency"]["agents"]}
    assert set(rows["role"]) <= {"id", "ref", "name"}
    assert set(rows["lead"]) <= {"id", "ref", "name"}
    assert "_agent_patches" not in mas

    role = apply_agency_entry_overlay(
        {"spec": {"skills": ["own"]}}, rows["role"], agent_patch=patches.get("role")
    )
    lead = apply_agency_entry_overlay({"spec": {}}, rows["lead"], agent_patch=patches.get("lead"))
    assert role["spec"]["skills"] == ["own", "recv-a", "recv-b"]
    assert lead["spec"]["skills"] == ["orch-a", "orch-b"]

    mas_for_validate = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "m"},
        "spec": mas["spec"],
    }
    result = validate_data(mas_for_validate, resolve_refs=False)
    assert result.ok, [str(i) for i in result.issues]



def test_apply_agency_entry_overlay_plain_tools_list_still_adds_not_replaces():
    """Unchanged, long-standing behavior: a plain list (no $op) is an ADD,
    dedup-merged onto the existing tools -- not a replace."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "d"}, "spec": {"tools": ["calc"]}}
    entry = {"id": "d", "tools": ["web-search"]}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["tools"] == ["calc", "web-search"]


def test_merge_tool_ref_list_keeps_entries_without_key(caplog):
    from mas.ctl.manifest.mas_agent_merge import _merge_tool_ref_list

    caplog.set_level("WARNING")
    merged = _merge_tool_ref_list([], [{"params": {"x": 1}}])
    assert merged == [{"params": {"x": 1}}]
    assert "no ref/name" in caplog.text


def test_apply_agency_entry_overlay_plain_context_value_replaces_regardless_of_shape():
    """A plain (non-$op) chunk patch value is an implicit full replace, even
    when it changes shape (e.g. {ref} -> inline string) -- same ergonomics as
    every other overlay merge strategy in this repo."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "a"},
        "spec": {"context": {"role": {"ref": "role.md"}}},
    }
    entry = {"id": "a", "spec": {"context": {"role": "inline role"}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["context"]["role"] == "inline role"


def test_apply_agency_entry_overlay_plain_array_context_value_replaces_not_adds():
    """A plain array chunk patch (no `$op`) fully replaces, mirroring the
    plain-string case above -- it does NOT append onto the base fragments."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "a"},
        "spec": {"context": {"role": ["a", "b"]}},
    }
    entry = {"id": "a", "spec": {"context": {"role": ["c", "d"]}}}
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["context"]["role"] == ["c", "d"]


def test_apply_agency_entry_overlay_context_op_add_appends_without_duplicating():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "a"},
        "spec": {"context": {"role": "You are a triage agent."}},
    }
    entry = {
        "id": "a",
        "spec": {"context": {"role": {"$op": {"add": ["Escalate P1s immediately."]}}}},
    }
    merged = apply_agency_entry_overlay(manifest, entry)
    assert merged["spec"]["context"]["role"] == [
        "You are a triage agent.",
        "Escalate P1s immediately.",
    ]


def test_apply_agency_entry_overlay_merges_governance_and_observability():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {
        "metadata": {"name": "a"},
        "spec": {
            "governance": ["sample_governance"],
            "observability": ["otel"],
        },
    }
    entry = {
        "id": "a",
        "spec": {
            "governance": [{"gov_no_undeclared_tool": {}}],
            "observability": ["native"],
        },
    }
    merged = apply_agency_entry_overlay(manifest, entry)
    gov = merged["spec"]["governance"]
    names = [g if isinstance(g, str) else next(iter(g)) for g in gov]
    assert "sample_governance" in names
    assert "gov_no_undeclared_tool" in names
    obs = merged["spec"]["observability"]
    assert "otel" in obs
    assert "native" in obs


def test_apply_agency_entry_overlay_empty_governance_clears():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    manifest = {"metadata": {"name": "a"}, "spec": {"governance": ["sample_governance"]}}
    merged = apply_agency_entry_overlay(manifest, {"id": "a", "spec": {"governance": []}})
    assert merged["spec"]["governance"] == []


def test_fanout_agency_row_merges_onto_agent_yaml():
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay
    from mas.ctl.overlay.merge import loaded_agent_patches, merge_overlay

    mas = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                ]
            }
        },
    }
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "with-guardrail"},
        "spec": {
            "target": {"kind": "Agent"},
            "patch": {
                "observability": ["native"],
                "governance": [{"sample_governance": {"policies": [{"name": "forbidden-destination"}]}}],
            },
        },
    }
    fanned = merge_overlay(mas, overlay)
    entry = fanned["spec"]["agency"]["agents"][0]
    assert set(entry) <= {"id", "ref", "name"}
    assert "_agent_patches" not in fanned
    agent = {
        "metadata": {"name": "moderator"},
        "spec": {"governance": ["sample_governance"], "observability": ["otel"]},
    }
    merged = apply_agency_entry_overlay(
        agent, entry, agent_patch=loaded_agent_patches(overlay, fanned).get("moderator")
    )
    names = [g if isinstance(g, str) else next(iter(g)) for g in merged["spec"]["governance"]]
    assert "sample_governance" in names
    assert merged["spec"]["observability"] == ["otel", "native"] or set(merged["spec"]["observability"]) == {
        "otel",
        "native",
    }


def test_agency_entries_by_id_prefers_agency_bucket():
    from mas.ctl.manifest.mas_agent_merge import _agency_entries_by_id

    mas = {
        "spec": {
            "agency": {"agents": [{"id": "a", "description": "agency wins"}]},
            "agents": [{"id": "a", "description": "spec loses"}],
        }
    }
    entry = _agency_entries_by_id(mas)["a"]
    assert entry["description"] == "agency wins"


def test_wire_entry_engine_delegation_uses_overlay_peer_description(tmp_path: Path):
    peer_yaml = tmp_path / "agents" / "alpha.yaml"
    peer_yaml.parent.mkdir(parents=True)
    peer_yaml.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "alpha"},
                "spec": {"description": "Base peer description."},
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "metadata": {"name": "entry"},
        "spec": {
            "workflow": {
                "entry": "entry",
                "nodes": [{"id": "entry", "delegates_to": ["alpha"]}],
            },
        },
    }
    mas = {
        "spec": {
            "agency": {
                "agents": [
                    {
                        "id": "alpha",
                        "ref": "agents/alpha.yaml",
                        "spec": {"description": "Overlay peer description."},
                    }
                ]
            }
        }
    }
    engine = LiveLlmEngine(manifest=manifest, use_tool_loop=True)
    wire_entry_engine_delegation(
        engine,
        manifest,
        tmp_path,
        comm=_StubComm(),
        entry_agent_id="entry",
        mas_config=mas,
        mas_base_dir=tmp_path,
    )
    assert engine.delegation_peer_descriptions == {"alpha": "Overlay peer description."}


def test_create_agent_runtime_applies_mas_overlay_context(monkeypatch, tmp_path: Path):
    from mas.ctl.compose.backends.mas_runtime_py import MasRuntimePyKernelBackend
    from mas.ctl.compose.models import AgentBindSlice, ComposedApplication, EffectiveBindManifest
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "ci-test-unused")
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)

    agent_yaml = tmp_path / "agents" / "moderator" / "agent.yaml"
    agent_yaml.parent.mkdir(parents=True)
    agent_yaml.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "moderator"},
                "spec": {
                    "context": {"role": "base"},
                    "design_pattern": {"type": "react"},
                    "models": [{"id": "main", "model": "gpt-4o"}],
                },
            }
        ),
        encoding="utf-8",
    )
    mas = {
        "metadata": {"name": "trip"},
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator/agent.yaml"},
                ]
            }
        },
    }
    bind = EffectiveBindManifest(
        mas_id="trip",
        spec_revision="",
        runtime_id="mas-runtime-py",
        deployment_name="local-inproc",
        agents=[
            AgentBindSlice(
                agent_id="moderator",
                manifest_path=str(agent_yaml.relative_to(tmp_path)),
            )
        ],
        composed_application=ComposedApplication(
            mas_id="trip",
            config=mas,
            agent_patches={"moderator": {"context": {"role": "from overlay"}}},
        ),
        mas_base_dir=tmp_path,
    )
    instance = MasRuntimePyKernelBackend(resolved_infra=infra).create_agent_runtime(bind, "moderator")
    from mas.runtime.engine.leaf import leaf_engine

    leaf = leaf_engine(instance.driver.engine)
    assert leaf.manifest["spec"]["context"]["role"] == "from overlay"
