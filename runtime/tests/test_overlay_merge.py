#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Overlay merge tests."""

import pytest
from mas.ctl.overlay.merge import (
    OverlayTargetError,
    apply_merge_patch,
    merge_context_chunk,
    merge_context_map,
    merge_overlay,
    overlay_runtime_semantics,
)


def _overlay(patch: dict, *, name: str = "test", target_kind: str = "Agent") -> dict:
    return {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": name},
        "spec": {"target": {"kind": target_kind}, "patch": patch},
    }


def test_merge_tools_overlay():
    base = {"spec": {"models": [{"model": "gpt-4"}]}}
    merged = merge_overlay(base, _overlay({"tools": {"$op": {"add": ["calc"]}}}))
    assert "calc" in merged["spec"]["tools"]


def test_merge_patch_format():
    base = {"spec": {"context": {"role": "base"}}}
    merged = merge_overlay(base, _overlay({"tools": {"$op": {"add": ["web"]}}}))
    assert "web" in merged["spec"]["tools"]


def test_merge_tools_dedupes():
    base = {"spec": {"tools": ["calc"]}}
    merged = merge_overlay(base, _overlay({"tools": {"$op": {"add": ["calc", "web"]}}}))
    assert merged["spec"]["tools"] == ["calc", "web"]


def test_merge_design_pattern():
    base = {"spec": {}}
    merged = merge_overlay(base, _overlay({"design_pattern": {"type": "cot"}}))
    assert merged["spec"]["design_pattern"]["type"] == "cot"


def test_merge_context_dict():
    base = {"spec": {"context": {"role": "a"}}}
    merged = merge_overlay(base, _overlay({"context": {"intent": "b"}}))
    assert merged["spec"]["context"] == {"role": "a", "intent": "b"}


def test_merge_context_op_add_appends_without_duplicating_base_text():
    base = {"spec": {"context": {"role": "You are a triage agent."}}}
    merged = merge_overlay(base, _overlay({"context": {"role": {"$op": {"add": ["Escalate P1s immediately."]}}}}))
    assert merged["spec"]["context"]["role"] == [
        "You are a triage agent.",
        "Escalate P1s immediately.",
    ]


def test_merge_context_op_remove():
    base = {"spec": {"context": {"role": ["a", "b", "c"]}}}
    merged = merge_overlay(base, _overlay({"context": {"role": {"$op": {"remove": ["b"]}}}}))
    assert merged["spec"]["context"]["role"] == ["a", "c"]


def test_merge_context_op_replace():
    base = {"spec": {"context": {"role": ["a", "b"]}}}
    merged = merge_overlay(base, _overlay({"context": {"role": {"$op": {"replace": ["z"]}}}}))
    assert merged["spec"]["context"]["role"] == ["z"]


def test_merge_context_op_clear():
    base = {"spec": {"context": {"role": ["a", "b"]}}}
    merged = merge_overlay(base, _overlay({"context": {"role": {"$op": {"clear": True}}}}))
    assert merged["spec"]["context"]["role"] == []


def test_merge_context_plain_value_still_fully_replaces_chunk():
    base = {"spec": {"context": {"role": "old role"}}}
    merged = merge_overlay(base, _overlay({"context": {"role": "new role"}}))
    assert merged["spec"]["context"]["role"] == "new role"


def test_merge_context_plain_array_still_fully_replaces_chunk():
    """A plain array patch (no `$op`) replaces the chunk wholesale, same as a
    plain string always has -- only `$op` opts into add/remove fragment merging."""
    base = {"spec": {"context": {"role": ["a", "b"]}}}
    merged = merge_overlay(base, _overlay({"context": {"role": ["c", "d"]}}))
    assert merged["spec"]["context"]["role"] == ["c", "d"]


def test_merge_context_chunk_add_appends_without_duplicating():
    merged = merge_context_chunk("You are a triage agent.", {"$op": {"add": ["Be concise."]}})
    assert merged == ["You are a triage agent.", "Be concise."]


def test_merge_context_chunk_add_is_idempotent():
    merged = merge_context_chunk(["a", "b"], {"$op": {"add": ["b", "c"]}})
    assert merged == ["a", "b", "c"]


def test_merge_context_chunk_remove():
    merged = merge_context_chunk(["a", "b", "c"], {"$op": {"remove": ["b"]}})
    assert merged == ["a", "c"]


def test_merge_context_chunk_replace():
    merged = merge_context_chunk(["a", "b"], {"$op": {"replace": ["z"]}})
    assert merged == ["z"]


def test_merge_context_chunk_clear():
    merged = merge_context_chunk(["a", "b"], {"$op": {"clear": True}})
    assert merged == []


def test_merge_context_chunk_plain_string_value_replaces():
    """No `$op` sugar -- implicit full replace, same ergonomics as list_ops."""
    assert merge_context_chunk("old role", "new role") == "new role"


def test_merge_context_chunk_plain_array_value_replaces():
    """A plain array (no `$op`) is also an implicit full replace, not an add --
    `$op` is what opts into fragment-level merging; a bare list is a new chunk
    value, same as a bare string or {ref} would be."""
    merged = merge_context_chunk(["a", "b"], ["c", "d"])
    assert merged == ["c", "d"]


def test_merge_context_chunk_plain_array_replaces_existing_string_chunk():
    merged = merge_context_chunk("old role", ["fragment one", "fragment two"])
    assert merged == ["fragment one", "fragment two"]


def test_merge_context_map_adds_new_key_and_patches_existing():
    base = {"role": "You are a triage agent."}
    patch = {"role": {"$op": {"add": ["Be concise."]}}, "intent": "Stay on task."}
    merged = merge_context_map(base, patch)
    assert merged == {
        "role": ["You are a triage agent.", "Be concise."],
        "intent": "Stay on task.",
    }


def test_merge_skills():
    base = {"spec": {"skills": ["s1"]}}
    merged = merge_overlay(base, _overlay({"skills": {"$op": {"add": ["s1", "s2"]}}}))
    assert merged["spec"]["skills"] == ["s1", "s2"]


def test_merge_tools_remove_via_op_remove():
    """tools_remove (a separate attribute) was removed as a concept: tools
    itself already supports {"$op": {"remove": [...]}} (list_ops), the same
    subtraction without a second, separately-named attribute."""
    base = {"spec": {"tools": ["calc", "web-search"]}}
    merged = merge_overlay(base, _overlay({"tools": {"$op": {"remove": ["web-search"]}}}))
    assert merged["spec"]["tools"] == ["calc"]


def test_merge_plugins_patch_is_rejected() -> None:
    import pytest

    base = {"spec": {"plugins": [{"name": "p1", "enabled": True}]}}
    with pytest.raises(OverlayTargetError, match="not in the schema"):
        merge_overlay(base, _overlay({"plugins": [{"name": "p1", "enabled": False}]}))


def test_merge_memory_and_seed():
    base = {"spec": {"memory_seed": [{"key": "k1", "content": "c1"}]}}
    merged = merge_overlay(
        base,
        _overlay({"memory": "letta", "memory_seed": {"$op": {"add": [{"key": "k2", "content": "c2"}]}}}),
    )
    assert merged["spec"]["memory"] == "letta"
    assert len(merged["spec"]["memory_seed"]) == 2


def test_merge_params_overlay_field():
    """params was a real, consumed field (params_sidecar.py, infra_pipeline.py)
    with no base-schema declaration until the Agent overlay-patch/base-schema
    unification -- confirms it's now a real, mergeable field end to end."""
    base = {"spec": {}}
    merged = merge_overlay(base, _overlay({"params": {"foo": "bar"}}))
    assert merged["spec"]["params"] == {"foo": "bar"}


def test_merge_context_policy_is_rejected_as_dead_field():
    """context_policy had zero consumers anywhere in the repo -- removed as
    dead schema during the Agent overlay-patch/base-schema unification."""
    import pytest

    base = {"spec": {}}
    with pytest.raises(OverlayTargetError, match="not in the schema"):
        merge_overlay(base, _overlay({"context_policy": {"foo": "bar"}}))


def test_merge_mas_params_field_was_a_real_gap_now_closed():
    """mas.schema.yaml didn't declare spec.params at all even though
    merge_mas_overlay always wrote patched params under spec.params --
    closed as part of the MAS overlay-patch/base-schema unification
    (same pattern as Agent's params gap)."""
    base = {"kind": "MAS", "spec": {}}
    merged = merge_overlay(base, _overlay({"params": {"foo": "bar"}}, target_kind="MAS"))
    assert merged["spec"]["params"] == {"foo": "bar"}


def test_merge_mas_capabilities_field_was_a_real_gap_now_closed():
    base = {"kind": "MAS", "spec": {}}
    merged = merge_overlay(base, _overlay({"capabilities": {"streaming": True}}, target_kind="MAS"))
    assert merged["spec"]["capabilities"] == {"streaming": True}


def test_merge_mas_governance_is_rejected_as_a_dead_field():
    """MAS-level governance was special-cased out of the generic merge
    dispatch but never had an actual merge implementation -- patching it
    was a silent no-op with zero test coverage. Removed as dead, mirroring
    context_policy's removal from the Agent overlay-patch fragment."""
    import pytest

    base = {"kind": "MAS", "spec": {}}
    with pytest.raises(OverlayTargetError, match="not in the schema"):
        merge_overlay(base, _overlay({"governance": ["policy-a"]}, target_kind="MAS"))


def test_merge_working_memory_persistent():
    base = {"spec": {}}
    merged = merge_overlay(base, _overlay({"working_memory": {"persistent": True}}))
    assert merged["spec"]["working_memory"] == {"persistent": True}


def test_merge_working_memory_persistent_merges_over_existing_block():
    base = {"spec": {"working_memory": {"persistent": True}}}
    merged = merge_overlay(base, _overlay({"working_memory": {"persistent": False}}))
    assert merged["spec"]["working_memory"] == {"persistent": False}


def test_merge_governance_null_removes_key():
    base = {"spec": {"governance": ["policy-a", {"policy-b": {}}]}}
    merged = merge_overlay(base, _overlay({"governance": {"$op": {"remove": ["policy-a"], "add": ["policy-c"]}}}))
    assert merged["spec"]["governance"] == [{"policy-b": {}}, "policy-c"]


def test_merge_metadata_only_overlay_unchanged():
    base = {"metadata": {"name": "a"}, "spec": {"tools": ["x"]}}
    merged = merge_overlay(base, {"metadata": {"name": "ov1"}})
    assert merged["metadata"]["name"] == "a"
    assert merged["spec"]["tools"] == ["x"]


def test_apply_merge_patch_null_deletes():
    target = {"a": 1, "b": 2}
    patch = {"b": None, "c": 3}
    result = apply_merge_patch(target, patch)
    assert result == {"a": 1, "c": 3}


def test_apply_merge_patch_replaces_non_dict_target():
    result = apply_merge_patch([], {"x": 1})
    assert result == {"x": 1}


def test_merge_llm_block():
    base = {"spec": {"llm": {"temperature": 0.1}}}
    merged = merge_overlay(base, _overlay({"llm": {"max_tokens": 100}}))
    assert merged["spec"]["llm"]["temperature"] == 0.1
    assert merged["spec"]["llm"]["max_tokens"] == 100


def test_merge_context_manager_list():
    base = {"spec": {"context_manager": {"memory": ["a"]}}}
    merged = merge_overlay(base, _overlay({"context_manager": {"memory": ["b"]}}))
    assert merged["spec"]["context_manager"]["memory"] == ["a", "b"]


def test_merge_assembler_type_keeps_existing_params():
    base = {
        "spec": {
            "assembler": {
                "type": "assembler",
                "params": {"emit_segments": True},
            }
        }
    }
    merged = merge_overlay(base, _overlay({"assembler": {"type": "assembler"}}))
    assert merged["spec"]["assembler"]["type"] == "assembler"
    assert merged["spec"]["assembler"]["params"]["emit_segments"] is True


def test_merge_context_manager_type_change_drops_params():
    base = {
        "spec": {
            "context_manager": {
                "type": "summarising",
                "params": {"keep_turns": 10, "hysteresis_ratio": 0.2},
            }
        }
    }
    merged = merge_overlay(base, _overlay({"context_manager": {"type": "stack"}}))
    assert merged["spec"]["context_manager"] == {"type": "stack"}


def test_merge_design_pattern_type_change_drops_params():
    base = {
        "spec": {
            "design_pattern": {
                "type": "react",
                "params": {"max_steps": 8, "parallel": False},
            }
        }
    }
    merged = merge_overlay(base, _overlay({"design_pattern": {"type": "cot"}}))
    assert merged["spec"]["design_pattern"] == {"type": "cot"}


def test_merge_nested_context_manager_params_keeps_siblings():
    base = {
        "spec": {
            "context_manager": {
                "type": "summarising",
                "params": {
                    "keep_turns": 10,
                    "trimmer": {"max_tokens": 128000, "reserve_tokens": 2000},
                },
            }
        }
    }
    merged = merge_overlay(
        base,
        _overlay({"context_manager": {"params": {"trimmer": {"reserve_tokens": 4000}}}}),
    )
    trimmer = merged["spec"]["context_manager"]["params"]["trimmer"]
    assert trimmer == {"max_tokens": 128000, "reserve_tokens": 4000}
    assert merged["spec"]["context_manager"]["params"]["keep_turns"] == 10


def test_merge_invalid_plugin_binding_raises():
    import pytest
    from mas.runtime.spec.plugin_binding import PluginBindingError

    base = {"spec": {"context_manager": ["stack"]}}
    with pytest.raises(PluginBindingError, match="plugin binding"):
        merge_overlay(base, _overlay({"context_manager": {"params": {"max_messages": 1}}}))


def test_merge_string_context_manager_keeps_type_when_params_patched():
    base = {"spec": {"context_manager": "stack"}}
    merged = merge_overlay(base, _overlay({"context_manager": {"params": {"max_messages": 50}}}))
    assert merged["spec"]["context_manager"]["type"] == "stack"
    assert merged["spec"]["context_manager"]["params"]["max_messages"] == 50


def test_merge_string_assembler_keeps_type_when_params_patched():
    base = {"spec": {"assembler": "assembler"}}
    merged = merge_overlay(base, _overlay({"assembler": {"params": {"emit_segments": False}}}))
    assert merged["spec"]["assembler"]["type"] == "assembler"
    assert merged["spec"]["assembler"]["params"]["emit_segments"] is False


def test_merge_no_spec_in_overlay():
    base = {"spec": {"tools": ["x"]}}
    merged = merge_overlay(base, {"metadata": {"name": "ov"}})
    assert merged["spec"]["tools"] == ["x"]


def test_normalize_rejects_shorthand_overlay():
    import pytest
    from mas.ctl.overlay.normalize import normalize_overlay

    with pytest.raises(ValueError, match="mas/v1"):
        normalize_overlay({"spec": {"tools": ["calc"]}})


def test_merge_mas_overlay_patches_agency_agent_context():
    base = {
        "kind": "MAS",
        "spec": {"agency": {"agents": [{"id": "moderator", "ref": "agents/moderator/agent.yaml"}]}},
    }
    overlay = _overlay(
        {
            "agents": {
                "moderator": {
                    "context": {"role": "patched role"},
                    "memory_seed": {"$op": {"add": [{"key": "f001", "content": "seed"}]}},
                }
            }
        },
        target_kind="MAS",
    )
    merged = merge_overlay(base, overlay)
    agent = merged["spec"]["agency"]["agents"][0]
    assert set(agent) <= {"id", "ref", "name"}
    patch = _patches(overlay, merged)["moderator"]
    assert patch["context"]["role"] == "patched role"
    assert patch["memory_seed"] == {"$op": {"add": [{"key": "f001", "content": "seed"}]}}


def _trip_mas(*, entry: str = "moderator") -> dict:
    return {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"id": "schedule_agent", "ref": "agents/schedule.yaml"},
                ]
            },
            "workflow": {"entry": entry, "nodes": [{"id": "moderator"}, {"id": "schedule_agent"}]},
        },
    }


def test_merge_mas_overlay_entry_patches_workflow_entry_only():
    overlay = _overlay(
        {"agents": {"$entry": {"design_pattern": {"type": "cot", "config": {"max_steps": 10}}}}},
        target_kind="MAS",
    )
    merged = merge_overlay(_trip_mas(), overlay)
    by_id = {a["id"]: a for a in merged["spec"]["agency"]["agents"]}
    assert _patches(overlay, merged)["moderator"]["design_pattern"] == {
        "type": "cot",
        "config": {"max_steps": 10},
    }
    assert "spec" not in by_id["moderator"]
    assert "spec" not in by_id["schedule_agent"]
    assert "schedule_agent" not in (_patches(overlay, merged))


def test_merge_mas_overlay_entry_follows_this_overlay_workflow_patch():
    overlay = _overlay(
        {
            "workflow": {"entry": "schedule_agent", "nodes": [{"id": "schedule_agent"}, {"id": "moderator"}]},
            "agents": {"$entry": {"design_pattern": {"type": "react"}}},
        },
        target_kind="MAS",
    )
    merged = merge_overlay(_trip_mas(entry="moderator"), overlay)
    by_id = {a["id"]: a for a in merged["spec"]["agency"]["agents"]}
    assert _patches(overlay, merged)["schedule_agent"]["design_pattern"] == {"type": "react"}
    assert "spec" not in by_id["schedule_agent"]
    assert "spec" not in by_id["moderator"]
    assert "moderator" not in (_patches(overlay, merged))


def test_merge_mas_overlay_entry_requires_workflow_entry():
    import pytest

    base = {
        "kind": "MAS",
        "spec": {"agency": {"agents": [{"id": "moderator", "ref": "agents/moderator.yaml"}]}},
    }
    overlay = _overlay({"agents": {"$entry": {"design_pattern": {"type": "cot"}}}}, target_kind="MAS")
    with pytest.raises(OverlayTargetError, match=r"patch\.agents\.\$entry requires spec\.workflow\.entry"):
        merge_overlay(base, overlay)


def test_merge_mas_overlay_entry_unknown_agency_id_fails():
    import pytest

    overlay = _overlay({"agents": {"$entry": {"design_pattern": {"type": "cot"}}}}, target_kind="MAS")
    with pytest.raises(OverlayTargetError, match="not in spec.agency.agents"):
        merge_overlay(_trip_mas(entry="missing"), overlay)


def test_merge_mas_overlay_entry_conflicts_with_named_id():
    import pytest

    overlay = _overlay(
        {
            "agents": {
                "$entry": {"design_pattern": {"type": "cot"}},
                "moderator": {"design_pattern": {"type": "react"}},
            }
        },
        target_kind="MAS",
    )
    with pytest.raises(OverlayTargetError, match=r"patch\.agents\.\$entry and patch\.agents\['moderator'\]"):
        merge_overlay(_trip_mas(), overlay)


def test_merge_mas_overlay_keeps_name_only_agents():
    base = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"name": "helper", "ref": "agents/helper.yaml"},
                ]
            }
        },
    }
    overlay = _overlay(
        {"agents": {"moderator": {"context": {"role": "patched role"}}}},
        target_kind="MAS",
    )
    merged = merge_overlay(base, overlay)
    agents = merged["spec"]["agency"]["agents"]
    assert len(agents) == 2
    by_key = {a.get("id") or a.get("name"): a for a in agents}
    assert "spec" not in by_key["moderator"]
    assert _patches(overlay, merged)["moderator"]["context"]["role"] == "patched role"
    assert by_key["helper"]["ref"] == "agents/helper.yaml"


def test_merge_mas_overlay_agent_collection_remove_by_name():
    base = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"name": "helper", "ref": "agents/helper.yaml"},
                ]
            }
        },
    }
    merged = merge_overlay(base, _overlay({"agents": {"$op": {"remove": ["helper"]}}}, target_kind="MAS"))
    agents = merged["spec"]["agency"]["agents"]
    assert len(agents) == 1
    assert agents[0]["id"] == "moderator"


def _trip_mas_with_delegates(*, entry: str = "moderator") -> dict:
    return {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"id": "finance", "ref": "agents/finance.yaml"},
                    {"id": "deal_desk", "ref": "agents/deal-desk.yaml"},
                ]
            },
            "workflow": {
                "entry": entry,
                "nodes": [
                    {"id": "moderator", "delegates_to": ["finance", "deal_desk"]},
                    {"id": "finance"},
                    {"id": "deal_desk"},
                ],
            },
        },
    }


def _patches(overlay: dict, merged: dict) -> dict:
    from mas.ctl.overlay.merge import loaded_agent_patches

    return loaded_agent_patches(overlay, merged)


def _skills(merged: dict, agent_id: str, overlay: dict):
    """Effective skills once the overlay patch is applied to agent YAML."""
    from mas.ctl.manifest.mas_agent_merge import apply_agency_entry_overlay

    return apply_agency_entry_overlay(
        {"spec": {}}, {"id": agent_id}, agent_patch=_patches(overlay, merged).get(agent_id)
    )["spec"].get("skills")


def test_merge_mas_overlay_not_entry_skips_chair():
    overlay = _overlay(
        {"agents": {"$not-entry": {"skills": {"$op": {"add": ["l9-concord-v2-receiver"]}}}}},
        target_kind="MAS",
    )
    merged = merge_overlay(_trip_mas_with_delegates(), overlay)
    assert "spec" not in {item["id"]: item for item in merged["spec"]["agency"]["agents"]}["moderator"]
    assert _skills(merged, "finance", overlay) == ["l9-concord-v2-receiver"]
    assert _skills(merged, "deal_desk", overlay) == ["l9-concord-v2-receiver"]


def test_merge_mas_overlay_all_then_entry_stacks():
    overlay = _overlay(
        {
            "agents": {
                "$all": {"budget": {"max_llm_calls": 40}},
                "$entry": {"skills": {"$op": {"add": ["l9-concord-v2-orchestrator"]}}},
                "$not-entry": {"skills": {"$op": {"add": ["l9-concord-v2-receiver"]}}},
            }
        },
        target_kind="MAS",
    )
    merged = merge_overlay(_trip_mas_with_delegates(), overlay)
    by_id = {item["id"]: item for item in merged["spec"]["agency"]["agents"]}
    assert "spec" not in by_id["moderator"]
    assert "spec" not in by_id["finance"]
    assert _patches(overlay, merged)["moderator"]["budget"] == {"max_llm_calls": 40}
    assert _patches(overlay, merged)["finance"]["budget"] == {"max_llm_calls": 40}
    assert _skills(merged, "moderator", overlay) == ["l9-concord-v2-orchestrator"]
    assert _skills(merged, "finance", overlay) == ["l9-concord-v2-receiver"]


def test_merge_mas_overlay_delegates_matches_workflow_edges():
    overlay = _overlay(
        {"agents": {"$delegates": {"skills": {"$op": {"add": ["l9-concord-v2-receiver"]}}}}},
        target_kind="MAS",
    )
    merged = merge_overlay(_trip_mas_with_delegates(), overlay)
    assert _skills(merged, "finance", overlay) == ["l9-concord-v2-receiver"]
    assert _skills(merged, "deal_desk", overlay) == ["l9-concord-v2-receiver"]
    assert "spec" not in {item["id"]: item for item in merged["spec"]["agency"]["agents"]}["moderator"]


def test_merge_mas_overlay_all_and_delegates_compose_on_same_agent_keep_op():
    """Two alias patches landing on the SAME agent (here $all + $delegates
    both touch finance/deal_desk) must stack -- not silently drop one -- via
    _resolve_mas_agent_patches._layer/_keep_unresolved_ops."""
    overlay = _overlay(
        {
            "agents": {
                "$all": {"skills": {"$op": {"add": ["base-skill"]}}},
                "$delegates": {"skills": {"$op": {"add": ["delegate-skill"]}}},
            }
        },
        target_kind="MAS",
    )
    merged = merge_overlay(_trip_mas_with_delegates(), overlay)
    assert _skills(merged, "finance", overlay) == ["base-skill", "delegate-skill"]
    assert _skills(merged, "deal_desk", overlay) == ["base-skill", "delegate-skill"]
    # moderator only ever matches $all (not a delegate of itself)
    assert _skills(merged, "moderator", overlay) == ["base-skill"]


def test_merge_mas_overlay_rejects_unsupported_patch_field() -> None:
    import pytest

    base = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "a", "ref": "agents/a.yaml"},
                ]
            }
        },
    }
    overlay = _overlay({"design_pattern": {"type": "cot"}}, target_kind="MAS")
    with pytest.raises(OverlayTargetError, match="not in the schema"):
        merge_overlay(base, overlay)


def test_merge_tools_explicit_ops_replace_add_remove_clear():
    base = {"spec": {"tools": ["calc", "web-search"]}}
    merged = merge_overlay(base, _overlay({"tools": {"$op": {"remove": ["calc"], "add": ["memory-search"]}}}))
    assert merged["spec"]["tools"] == ["web-search", "memory-search"]

    cleared = merge_overlay(base, _overlay({"tools": {"$op": {"clear": True}}}))
    assert cleared["spec"]["tools"] == []

    replaced = merge_overlay(base, _overlay({"tools": {"$op": {"replace": ["memory-search"]}}}))
    assert replaced["spec"]["tools"] == ["memory-search"]


def test_merge_skills_explicit_ops():
    base = {"spec": {"skills": ["s1", "s2"]}}
    merged = merge_overlay(base, _overlay({"skills": {"$op": {"remove": ["s1"], "add": ["s3"]}}}))
    assert merged["spec"]["skills"] == ["s2", "s3"]


def test_merge_memory_seed_explicit_ops():
    base = {"spec": {"memory_seed": [{"key": "k1", "content": "c1"}]}}
    merged = merge_overlay(base, _overlay({"memory_seed": {"$op": {"add": [{"key": "k2", "content": "c2"}]}}}))
    assert merged["spec"]["memory_seed"] == [
        {"key": "k1", "content": "c1"},
        {"key": "k2", "content": "c2"},
    ]


def test_merge_mas_agents_collection_explicit_ops():
    base = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"id": "helper", "ref": "agents/helper.yaml"},
                ]
            }
        },
    }
    merged = merge_overlay(
        base,
        _overlay(
            {"agents": {"$op": {"remove": ["helper"], "add": [{"id": "generalist", "ref": "agents/generalist.yaml"}]}}},
            target_kind="MAS",
        ),
    )
    assert [a["id"] for a in merged["spec"]["agency"]["agents"]] == ["moderator", "generalist"]


def test_composition_tools_replace_dominates_previous_add() -> None:
    base = {"spec": {"tools": ["a"]}}
    merged_once = merge_overlay(base, _overlay({"tools": {"$op": {"add": ["b"]}}}))
    merged_twice = merge_overlay(merged_once, _overlay({"tools": {"$op": {"replace": ["c"]}}}))
    assert merged_twice["spec"]["tools"] == ["c"]


def test_composition_tools_clear_then_add_is_deterministic() -> None:
    base = {"spec": {"tools": ["a", "b"]}}
    merged_once = merge_overlay(base, _overlay({"tools": {"$op": {"clear": True}}}))
    merged_twice = merge_overlay(merged_once, _overlay({"tools": {"$op": {"add": ["c"]}}}))
    assert merged_twice["spec"]["tools"] == ["c"]


def test_composition_control_merge_then_replace_is_deterministic() -> None:
    base = {"spec": {"control": {"budget": {"max_tokens": 10}}}}
    merged_once = merge_overlay(
        base, _overlay({"control": {"$op": {"merge": {"rate_limiter": {"requests_per_minute": 5}}}}})
    )
    merged_twice = merge_overlay(
        merged_once, _overlay({"control": {"$op": {"replace": {"budget": {"max_tokens": 99}}}}})
    )
    assert merged_twice["spec"]["control"] == {"budget": {"max_tokens": 99}}


def test_list_field_accepts_implicit_array_replace() -> None:
    base = {"spec": {"skills": ["s1"]}}
    merged = merge_overlay(base, _overlay({"skills": ["s2"]}))
    assert merged["spec"]["skills"] == ["s2"]


def test_agent_overlay_rejects_provider_wiring() -> None:
    """Provider connections are infra, not agent overlay patch data."""
    base = {"spec": {}}
    with pytest.raises(OverlayTargetError, match="providers"):
        merge_overlay(
            base,
            _overlay({"providers": [{"name": "mcp", "kind": "mcp", "url": "http://127.0.0.1:9001/mcp"}]}),
        )


def test_runtime_semantics_registry_covers_non_trivial_agent_fields() -> None:
    semantics = overlay_runtime_semantics()
    agent = semantics["Agent"]
    for field in (
        "tools",
        "skills",
        "memory_seed",
        "observability",
        "governance",
        "control",
    ):
        assert field in agent
    assert "providers" not in agent
    assert agent["models"] == "named_list_merge(identity=id)"
    assert agent["description"] == "replace"
    assert agent["tools_ref"] == "replace"
    assert agent["behavior"] == "mapping_merge_or_ops"
    assert "Infra" in semantics


def test_merge_models_reasoning_deep_merges_by_id() -> None:
    base = {"spec": {"models": [{"id": "main", "model": "gpt-5-mini", "temperature": 0.2}]}}
    merged = merge_overlay(
        base,
        _overlay({"models": [{"id": "main", "reasoning": {"effort": "low", "think": True, "mode": "standard"}}]}),
    )
    row = merged["spec"]["models"][0]
    assert row["model"] == "gpt-5-mini"
    assert row["temperature"] == 0.2
    assert row["reasoning"]["effort"] == "low"
    assert row["reasoning"]["think"] is True
    assert row["reasoning"]["mode"] == "standard"


def test_merge_models_sampling_and_extra_by_id() -> None:
    base = {"spec": {"models": [{"id": "main", "model": "gpt-5-mini", "temperature": 0.7, "extra": {"a": 1}}]}}
    merged = merge_overlay(
        base,
        _overlay(
            {
                "models": [
                    {
                        "id": "main",
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "extra": {"b": 2},
                    }
                ]
            }
        ),
    )
    row = merged["spec"]["models"][0]
    assert row["model"] == "gpt-5-mini"
    assert row["temperature"] == 0.2
    assert row["top_p"] == 0.9
    assert row["extra"] == {"a": 1, "b": 2}


def test_merge_agent_overlay_patches_description_tools_ref_behavior() -> None:
    base = {
        "spec": {
            "description": "base",
            "tools_ref": "travel-tools",
            "behavior": {"share_reasoning": False},
        }
    }
    merged = merge_overlay(
        base,
        _overlay(
            {
                "description": "patched",
                "tools_ref": "hotels-tools",
                "behavior": {"share_reasoning": True},
            }
        ),
    )
    assert merged["spec"]["description"] == "patched"
    assert merged["spec"]["tools_ref"] == "hotels-tools"
    assert merged["spec"]["behavior"]["share_reasoning"] is True


def test_merge_infra_overlay_json_merge_patch_semantics() -> None:
    base = {
        "apiVersion": "mas/v1",
        "kind": "Infra",
        "metadata": {"name": "default"},
        "spec": {
            "proxy": {"api_base": "https://api.example", "api_key_env": "OPENAI_API_KEY"},
            "models": {"allowed": ["gpt-4o-mini"]},
            "tools": {"web": {"kind": "python"}},
        },
    }
    ov = _overlay(
        {
            "proxy": {"api_base": "https://new.example"},
            "models": {"allowed": ["gpt-4o"]},
            "tools": {"web": None, "calc": {"kind": "python"}},
        },
        target_kind="Infra",
    )
    merged = merge_overlay(base, ov)
    assert merged["spec"]["proxy"] == {
        "api_base": "https://new.example",
        "api_key_env": "OPENAI_API_KEY",
    }
    assert merged["spec"]["models"] == {"allowed": ["gpt-4o"]}
    assert merged["spec"]["tools"] == {"calc": {"kind": "python"}}


def _gov_names(spec: dict) -> list[str]:
    names = []
    for item in spec.get("governance") or []:
        names.append(item if isinstance(item, str) else next(iter(item)))
    return names


def test_agent_overlay_fans_out_onto_mas_agency_rows() -> None:
    """run-mas/compose only merge_overlay the MAS document. Agent overlays
    must land on nested agency rows, not on MAS.spec (unsupported)."""
    base = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"id": "helper", "ref": "agents/helper.yaml"},
                ]
            }
        },
    }
    overlay = _overlay(
        {
            "observability": ["native"],
            "governance": [{"sample_governance": {"policies": [{"name": "forbidden-destination"}]}}],
        }
    )
    merged = merge_overlay(base, overlay)
    assert "governance" not in (merged.get("spec") or {})
    assert "observability" not in (merged.get("spec") or {})
    for agent in merged["spec"]["agency"]["agents"]:
        assert agent["ref"].startswith("agents/")
        assert "spec" not in agent
        patch = _patches(overlay, merged)[agent["id"]]
        assert patch["observability"] == ["native"]
        assert "sample_governance" in _gov_names(patch)


def test_agent_overlay_on_mas_with_no_agents_raises() -> None:
    import pytest

    with pytest.raises(OverlayTargetError, match="matched no agents"):
        merge_overlay(
            {"kind": "MAS", "spec": {"agency": {"agents": []}}},
            _overlay({"observability": ["native"]}),
        )


def test_agent_overlay_target_name_filters_agency_row() -> None:
    base = {
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "moderator", "ref": "agents/moderator.yaml"},
                    {"id": "helper", "ref": "agents/helper.yaml"},
                ]
            }
        },
    }
    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "named"},
        "spec": {
            "target": {"kind": "Agent", "name": "helper"},
            "patch": {"observability": ["native"]},
        },
    }
    merged = merge_overlay(base, overlay)
    by_id = {a["id"]: a for a in merged["spec"]["agency"]["agents"]}
    assert "spec" not in by_id["moderator"]
    assert "spec" not in by_id["helper"]
    assert "moderator" not in (_patches(overlay, merged))
    assert _patches(overlay, merged)["helper"]["observability"] == ["native"]


def test_agent_overlay_target_name_miss_raises() -> None:
    import pytest

    with pytest.raises(OverlayTargetError, match="named 'missing'"):
        merge_overlay(
            _trip_mas(),
            {
                "apiVersion": "mas/v1",
                "kind": "Overlay",
                "metadata": {"name": "named"},
                "spec": {
                    "target": {"kind": "Agent", "name": "missing"},
                    "patch": {"observability": ["native"]},
                },
            },
        )


def test_agent_overlay_on_agent_document_is_unchanged() -> None:
    base = {"kind": "Agent", "spec": {"tools": ["calc"]}}
    merged = merge_overlay(base, _overlay({"observability": ["native"]}))
    assert merged["spec"]["observability"] == ["native"]
    assert merged["spec"]["tools"] == ["calc"]
