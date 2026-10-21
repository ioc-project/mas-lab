#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compile overlays + runtime defaults into a resolved Agent/MAS spec."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.ctl.compile import (
    CompileError,
    as_bundle_document,
    compile_manifest,
    compiled_documents,
    fill_agent_defaults,
    fill_mas_defaults,
    resolve_layout,
    write_compiled,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIAL_1 = REPO_ROOT / "docs/tutorials/01-building-an-agent"
TUTORIAL_2 = REPO_ROOT / "docs/tutorials/02-creating-a-mas"


def _tool_refs(agent: dict) -> list[str]:
    tools = (agent.get("spec") or {}).get("tools") or []
    refs: list[str] = []
    for item in tools:
        if isinstance(item, str):
            refs.append(item)
        elif isinstance(item, dict) and item.get("ref"):
            refs.append(str(item["ref"]))
    return refs


def test_fill_agent_defaults_fills_omitted_runtime_fields() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "bare"},
        "spec": {"description": "blank slate"},
    }
    filled = fill_agent_defaults(doc)
    spec = filled["spec"]
    assert spec["design_pattern"]["type"]
    assert "max_steps" not in spec["design_pattern"]["params"]
    assert spec["design_pattern"]["params"]["max_cot_pass"] == 1
    assert spec["design_pattern"]["params"]["parallel"] is True
    assert spec["models"][0]["model"] == "any"
    assert "context_window" not in spec["models"][0]
    cm = spec["context_manager"]
    assert cm["type"] == "summarising"
    assert cm["params"]["keep_turns"] == 10
    assert cm["params"]["hysteresis_ratio"] == 0.1
    assert "max_tokens" not in cm["params"]["trimmer"]
    assert cm["params"]["trimmer"]["reserve_tokens"] == 2000
    assert cm["params"]["trimmer"]["safety_margin_ratio"] == 0.1
    assert spec["assembler"]["type"] == "assembler"
    assert spec["assembler"]["params"]["emit_segments"] is True
    assert spec["assembler"]["params"]["always_reassemble"] is False
    assert "design_pattern" not in doc["spec"]


def test_fill_mas_defaults_writes_any() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "team"},
        "spec": {"agency": {"agents": []}},
    }
    filled = fill_mas_defaults(doc)
    assert filled["spec"]["models"][0] == {"id": "main", "model": "any"}
    pinned = fill_mas_defaults(
        {
            "apiVersion": "mas/v1",
            "kind": "MAS",
            "metadata": {"name": "team"},
            "spec": {"models": [{"id": "main", "model": "gpt-4o"}]},
        }
    )
    assert pinned["spec"]["models"][0]["model"] == "gpt-4o"


def test_fill_agent_defaults_preserves_explicit_model() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {
            "description": "qa",
            "models": [{"model": "gpt-4o", "max_tokens": 1500, "context_window": 64000}],
            "design_pattern": {"type": "cot"},
        },
    }
    filled = fill_agent_defaults(doc)
    assert filled["spec"]["models"][0]["model"] == "gpt-4o"
    assert filled["spec"]["design_pattern"]["type"] == "cot"
    trimmer = filled["spec"]["context_manager"]["params"]["trimmer"]
    assert trimmer["max_tokens"] == 64000
    assert trimmer["reserve_tokens"] == 1500
    assert "summary_threshold" not in filled["spec"]["context_manager"]["params"]
    assert trimmer["safety_margin_ratio"] == 0.1
    assert filled["spec"]["assembler"]["type"] == "assembler"
    assert filled["spec"]["context_manager"]["params"]["summarizer"] == "llm"


def test_fill_agent_defaults_accepts_design_pattern_string_shorthand() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {"description": "qa", "design_pattern": "cot"},
    }
    filled = fill_agent_defaults(doc)
    assert filled["spec"]["design_pattern"]["type"] == "cot"
    assert "max_steps" not in filled["spec"]["design_pattern"]["params"]


def test_fill_agent_defaults_preserves_explicit_assembler() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {
            "description": "qa",
            "assembler": {"type": "assembler", "params": {"emit_segments": False}},
        },
    }
    filled = fill_agent_defaults(doc)
    assert filled["spec"]["assembler"]["type"] == "assembler"
    assert filled["spec"]["assembler"]["params"]["emit_segments"] is False


def test_fill_agent_defaults_rejects_removed_context_plugin() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {"description": "qa", "context_plugin": "assembler"},
    }
    with pytest.raises(CompileError, match="spec.assembler"):
        fill_agent_defaults(doc)


def test_fill_agent_defaults_translates_working_memory_compaction() -> None:
    """Compile must alias compaction before filling CM defaults, or keep_recent
    would be overwritten by the package summarising default."""
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "capped"},
        "spec": {
            "description": "capped",
            "working_memory": {"compaction": {"strategy": "keep_recent", "max_messages": 4}},
        },
    }
    filled = fill_agent_defaults(doc)
    cm = filled["spec"]["context_manager"]
    assert cm["type"] == "stack"
    assert cm["params"]["max_messages"] == 4
    assert "max_tokens" not in cm["params"]["trimmer"]


def test_fill_agent_defaults_compaction_summarize_model() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "cheap-sum"},
        "spec": {
            "description": "cheap-sum",
            "working_memory": {
                "compaction": {
                    "strategy": "summarize",
                    "model": "gpt-4o-mini",
                    "keep_turns": 4,
                }
            },
        },
    }
    filled = fill_agent_defaults(doc)
    params = filled["spec"]["context_manager"]["params"]
    assert filled["spec"]["context_manager"]["type"] == "summarising"
    assert params["keep_turns"] == 4
    assert params["summarizer"] == {"type": "llm", "params": {"model": "gpt-4o-mini"}}
    assert "summary_threshold" not in params


def test_fill_agent_defaults_preserves_summarizer_model_id() -> None:
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "sum-id"},
        "spec": {
            "description": "sum-id",
            "models": [
                {"id": "main", "model": "gpt-4o", "max_tokens": 2000},
                {"id": "summarizer", "model": "gpt-4o-mini"},
            ],
            "context_manager": {
                "type": "summarising",
                "params": {
                    "summarizer": {"type": "llm", "params": {"model": "summarizer"}},
                },
            },
        },
    }
    filled = fill_agent_defaults(doc)
    params = filled["spec"]["context_manager"]["params"]
    assert params["summarizer"]["params"]["model"] == "summarizer"
    assert "summary_threshold" not in params


def test_compile_tutorial_1_stacks_overlays() -> None:
    compiled = compile_manifest(
        TUTORIAL_1 / "agent.yaml",
        [
            TUTORIAL_1 / "overlays/tools.yaml",
            TUTORIAL_1 / "overlays/skills.yaml",
            TUTORIAL_1 / "overlays/memory.yaml",
        ],
        validate=False,
    )
    assert compiled.kind == "Agent"
    assert compiled.agent is not None
    spec = compiled.agent["spec"]
    assert spec["skills"] == ["answer-formatting"]
    assert spec["memory"] == "semantic"
    refs = _tool_refs(compiled.agent)
    assert "samples:tools/web-search.tool.yaml" in refs
    assert "samples:tools/calc.tool.yaml" in refs
    assert "samples:tools/memory-search.tool.yaml" in refs
    context = spec["context"]
    assert "tool_usage" not in context  # tool descriptions reach the model on their own
    assert "memory_usage" in context
    assert spec["models"][0]["model"] == "gpt-4o"
    assert spec["design_pattern"]["type"]
    assert spec["context_manager"]["type"] == "summarising"
    assert spec["context_manager"]["params"]["keep_turns"] == 10
    assert spec["context_manager"]["params"]["hysteresis_ratio"] == 0.1
    assert "max_tokens" not in spec["context_manager"]["params"]["trimmer"]
    assert spec["context_manager"]["params"]["trimmer"]["safety_margin_ratio"] == 0.1
    assert spec["assembler"]["type"] == "assembler"


def test_compile_rejects_mas_overlay_on_agent() -> None:
    with pytest.raises(CompileError, match="MAS overlay"):
        compile_manifest(
            TUTORIAL_1 / "agent.yaml",
            [TUTORIAL_2 / "overlays/linear.yaml"],
            validate=False,
        )


def test_compile_tutorial_2_linear_overlay_rewrites_topology() -> None:
    compiled = compile_manifest(
        TUTORIAL_2 / "mas.yaml",
        [TUTORIAL_2 / "overlays/linear.yaml"],
        validate=False,
    )
    assert compiled.kind == "MAS"
    assert compiled.agent_ids == ["moderator", "schedule_agent", "itinerary_agent", "concierge_agent"]
    workflow = (compiled.mas or {}).get("spec", {}).get("workflow") or {}
    assert workflow.get("entry") == "moderator"
    assert "moderator" in compiled.agents
    assert compiled.agents["moderator"]["spec"]["design_pattern"]["type"] == "deterministic_linear"
    bundle = as_bundle_document(compiled)
    inlined = bundle["spec"]["agency"]["agents"]
    assert [a["metadata"]["name"] for a in inlined] == compiled.agent_ids
    assert all(a.get("kind") == "Agent" for a in inlined)
    tree_docs = compiled_documents(compiled, "tree")
    assert set(tree_docs) >= {"mas.yaml", "agents/moderator/agent.yaml"}
    tree_agents = tree_docs["mas.yaml"]["spec"]["agency"]["agents"]
    assert all(set(row) <= {"id", "ref"} for row in tree_agents)
    assert tree_agents[0]["ref"] == "agents/moderator/agent.yaml"


def test_resolve_layout_auto_uses_path_shape(tmp_path: Path) -> None:
    assert resolve_layout(None, "auto") == "bundle"
    assert resolve_layout(tmp_path / "out.yaml", "auto") == "bundle"
    assert resolve_layout(tmp_path / "compiled", "auto") == "tree"
    with pytest.raises(CompileError, match="directory"):
        resolve_layout(tmp_path / "out.yaml", "tree")
    with pytest.raises(CompileError, match="requires --output"):
        resolve_layout(None, "tree")


def test_write_compiled_agent_file_and_mas_tree(tmp_path: Path) -> None:
    agent = compile_manifest(
        TUTORIAL_1 / "agent.yaml",
        [TUTORIAL_1 / "overlays/tools.yaml"],
        validate=False,
    )
    agent_out = tmp_path / "qa.yaml"
    written = write_compiled(agent, output=agent_out, layout="bundle", header=False)
    assert written == [agent_out]
    loaded = yaml.safe_load(agent_out.read_text(encoding="utf-8"))
    assert loaded["kind"] == "Agent"
    assert "samples:tools/web-search.tool.yaml" in _tool_refs(loaded)

    mas = compile_manifest(
        TUTORIAL_2 / "mas.yaml",
        [TUTORIAL_2 / "overlays/linear.yaml"],
        validate=False,
    )
    mas_dir = tmp_path / "team"
    written = write_compiled(mas, output=mas_dir, layout="tree", header=False)
    names = {path.name for path in written}
    assert "mas.yaml" in names
    assert (mas_dir / "agents/schedule-agent/agent.yaml").is_file()
    mas_doc = yaml.safe_load((mas_dir / "mas.yaml").read_text(encoding="utf-8"))
    assert mas_doc["spec"]["agency"]["agents"][0]["ref"] == "agents/moderator/agent.yaml"
