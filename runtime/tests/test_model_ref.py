#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.runtime.spec.model_ref import (
    first_nonempty,
    model_binding_by_id,
    resolve_model_ref,
)


def _spec(*models: dict) -> dict:
    return {"spec": {"models": list(models)}}


def test_empty_ref_is_the_agent_engine() -> None:
    assert resolve_model_ref({}, None, engine_model="gpt-4o") == ("gpt-4o", "agent")
    assert resolve_model_ref({}, "  ", engine_model="gpt-4o") == ("gpt-4o", "agent")
    assert resolve_model_ref({}, None, engine_model=None) == (None, "agent")


def test_models_id_resolves_to_provider_string() -> None:
    spec = _spec(
        {"id": "main", "model": "gpt-4o"},
        {"id": "summarizer", "model": "gpt-4o-mini"},
    )
    assert model_binding_by_id(spec, "summarizer")["model"] == "gpt-4o-mini"
    assert resolve_model_ref(spec, "summarizer", engine_model="gpt-4o") == (
        "gpt-4o-mini",
        "spec.models[id=summarizer]",
    )


def test_default_id_is_main() -> None:
    spec = _spec({"model": "gpt-4o"})
    assert model_binding_by_id(spec, "main")["model"] == "gpt-4o"


def test_bare_id_with_no_model_field_falls_back_to_agent() -> None:
    """Do not send a spec.models[].id to the provider as if it were a LiteLLM name."""
    spec = _spec({"id": "summarizer"})
    assert resolve_model_ref(spec, "summarizer", engine_model="gpt-4o") == (
        "gpt-4o",
        "agent",
    )


def test_engine_string_is_agent_not_override() -> None:
    spec = _spec({"id": "main", "model": "gpt-4o"})
    assert resolve_model_ref(spec, "gpt-4o", engine_model="gpt-4o") == (
        "gpt-4o",
        "agent",
    )


def test_unknown_token_is_literal_override() -> None:
    spec = _spec({"id": "main", "model": "gpt-4o"})
    assert resolve_model_ref(spec, "haiku-3", engine_model="gpt-4o") == (
        "haiku-3",
        "override",
    )


def test_accepts_raw_spec_or_full_manifest() -> None:
    raw = {"models": [{"id": "cheap", "model": "gpt-4o-mini"}]}
    assert resolve_model_ref(raw, "cheap", engine_model="gpt-4o")[0] == "gpt-4o-mini"


def test_primary_model_string_prefers_main() -> None:
    from mas.runtime.spec.model_ref import primary_model_string

    spec = _spec(
        {"id": "summarizer", "model": "gpt-4o-mini"},
        {"id": "main", "model": "gpt-4o"},
    )
    assert primary_model_string(spec) == "gpt-4o"
    assert primary_model_string({"spec": {"models": []}}) is None
    assert primary_model_string(_spec({"model": "any"})) is None


def test_first_concrete_skips_any() -> None:
    from mas.runtime.spec.model_ref import first_concrete

    assert first_concrete(("any", "a"), ("", "b"), ("gpt-4o", "experiment.model")) == (
        "gpt-4o",
        "experiment.model",
    )


def test_first_nonempty_skips_blank_and_reports_source() -> None:
    assert first_nonempty(
        ("  ", "a"),
        ("", "b"),
        (None, "c"),
        ("gpt-4o-mini", "experiment.evaluation.model"),
        ("ignored", "later"),
    ) == ("gpt-4o-mini", "experiment.evaluation.model")
    assert first_nonempty(("  ", "a"), (None, "b")) == (None, "")


def test_normalize_model_slots_scalar_is_main() -> None:
    from mas.runtime.spec.model_ref import normalize_model_slots

    assert normalize_model_slots(model="gpt-4o") == {"main": "gpt-4o"}
    assert normalize_model_slots(model="any") == {}
    assert normalize_model_slots(models={"summarizer": "gpt-4o-mini", "judge": "haiku"}) == {
        "summarizer": "gpt-4o-mini",
        "judge": "haiku",
    }
    assert normalize_model_slots(model="gpt-4o", models={"main": "gpt-4o-mini"}) == {"main": "gpt-4o-mini"}
    assert normalize_model_slots(model="gpt-4o", models={"main": "any", "judge": "haiku"}) == {
        "main": "gpt-4o",
        "judge": "haiku",
    }


def test_resolve_slot_agent_then_experiment() -> None:
    from mas.runtime.spec.model_ref import resolve_slot

    spec = _spec({"id": "summarizer", "model": "any"})
    assert resolve_slot(
        "summarizer",
        agent_spec=spec,
        model_slots={"summarizer": "gpt-4o-mini"},
        engine_model="gpt-4o",
    ) == ("gpt-4o-mini", "experiment.models.summarizer")
    pinned = _spec({"id": "summarizer", "model": "gpt-4o"})
    assert resolve_slot(
        "summarizer",
        agent_spec=pinned,
        model_slots={"summarizer": "gpt-4o-mini"},
        engine_model="gpt-4o",
    ) == ("gpt-4o", "spec.models[id=summarizer]")
    assert resolve_slot("summarizer", engine_model="gpt-4o") == ("gpt-4o", "agent")
