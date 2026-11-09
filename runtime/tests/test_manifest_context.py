#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""manifest_context resolution tests."""

from pathlib import Path

import pytest
from mas.runtime.boundary.context.manifest_context import (
    ContextChunkError,
    ContextRefNotFoundError,
    context_chunks_from_spec,
    resolve_context_chunk,
    routing_description_from_agent,
)


def test_routing_description_from_spec_description():
    manifest = {"spec": {"description": "Telemetry analyst for baselines."}}
    assert routing_description_from_agent(manifest) == "Telemetry analyst for baselines."


def test_context_chunks_support_ref_object(tmp_path: Path):
    role_file = tmp_path / "role.md"
    role_file.write_text("Role from file.", encoding="utf-8")
    spec = {"context": {"role": {"ref": "role.md"}, "intent": "Stay concise."}}
    chunks = context_chunks_from_spec(spec, base_dir=tmp_path)
    assert "[role] Role from file." in chunks
    assert "[intent] Stay concise." in chunks


def test_resolve_context_chunk_missing_ref_raises(tmp_path: Path):
    with pytest.raises(ContextRefNotFoundError, match="missing.md"):
        resolve_context_chunk({"ref": "missing.md"}, base_dir=tmp_path)


def test_resolve_context_chunk_bare_path_reads_existing_file(tmp_path: Path):
    role_file = tmp_path / "prompts" / "role.md"
    role_file.parent.mkdir(parents=True)
    role_file.write_text("Bare path role.", encoding="utf-8")
    assert resolve_context_chunk("prompts/role.md", base_dir=tmp_path) == "Bare path role."


def test_context_chunks_from_spec_skips_unsupported_chunk(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    spec = {"context": {"good": "ok", "bad": {"nope": True}}}
    chunks = context_chunks_from_spec(spec, base_dir=tmp_path)
    assert chunks == ["[good] ok"]
    assert any("skipping context key 'bad'" in r.message for r in caplog.records)


def test_resolve_context_chunk_unsupported_dict_raises():
    with pytest.raises(ContextChunkError, match="unsupported context chunk"):
        resolve_context_chunk({"nope": True}, base_dir=Path("/tmp"))


def test_resolve_context_chunk_missing_bare_path_warns(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    assert resolve_context_chunk("prompts/missing-role.md", base_dir=tmp_path) == ("prompts/missing-role.md")
    assert any("looks like a file path" in r.message for r in caplog.records)


def test_resolve_context_chunk_long_inline_role_with_slash(tmp_path: Path):
    """Multiline inline prompts with slashes are never probed as file paths."""
    role = (
        "You can look up schedules and attractions.\n"
        "Use get_attractions_description for detailed attraction/highlight listings.\n"
        "Provide a clear overview when done."
    )
    assert resolve_context_chunk(role, base_dir=tmp_path) == role


def test_resolve_context_chunk_long_inline_role_without_slash(tmp_path: Path):
    role = (
        "You are a moderator coordinating a team of specialized agents.\n"
        "Based on the conversation so far, decide whether to delegate or answer.\n"
        "Always include your reasoning before calling an agent or producing a final answer."
    )
    assert resolve_context_chunk(role, base_dir=tmp_path) == role


def test_resolve_context_chunk_array_joins_fragments(tmp_path: Path):
    value = ["You are a triage agent.", "Always cite your sources."]
    assert resolve_context_chunk(value, base_dir=tmp_path) == "You are a triage agent.\nAlways cite your sources."


def test_resolve_context_chunk_array_mixes_inline_and_ref(tmp_path: Path):
    escalation = tmp_path / "escalation.md"
    escalation.write_text("Confirm the budget before booking.", encoding="utf-8")
    value = ["You are a travel planner.", {"ref": "escalation.md"}]
    assert resolve_context_chunk(value, base_dir=tmp_path) == (
        "You are a travel planner.\nConfirm the budget before booking."
    )


def test_resolve_context_chunk_array_missing_ref_raises(tmp_path: Path):
    with pytest.raises(ContextRefNotFoundError, match="missing.md"):
        resolve_context_chunk(["ok", {"ref": "missing.md"}], base_dir=tmp_path)


def test_resolve_context_chunk_nested_array_raises(tmp_path: Path):
    with pytest.raises(ContextChunkError, match="nested array"):
        resolve_context_chunk(["ok", ["nested"]], base_dir=tmp_path)


def test_context_chunks_from_spec_resolves_array_chunk(tmp_path: Path):
    spec = {"context": {"role": ["You are a triage agent.", "Be concise."]}}
    chunks = context_chunks_from_spec(spec, base_dir=tmp_path)
    assert chunks == ["[role] You are a triage agent.\nBe concise."]
