#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""History budget = model context window minus completion reserve."""

from __future__ import annotations

from mas.library.standard.lib.context.history_budget import (
    assembly_trimmer_params,
    context_manager_history_budget_hint,
    derived_trimmer_params,
    fill_context_manager_defaults,
    history_token_budget,
    model_completion_tokens,
    model_context_window,
)


def test_defaults_when_models_omitted() -> None:
    spec = {"description": "bare"}
    assert model_context_window({"spec": spec}) == 128000
    assert model_completion_tokens({"spec": spec}) == 2000
    assert derived_trimmer_params({"spec": spec}) == (128000, 2000)
    assert history_token_budget({"spec": spec}) == 128000 - 2000 - 12800


def test_model_binding_by_id_and_resolve_model_ref() -> None:
    from mas.runtime.spec.model_ref import model_binding_by_id, resolve_model_ref

    spec = {
        "models": [
            {"id": "main", "model": "gpt-4o", "context_window": 128000},
            {"id": "summarizer", "model": "gpt-4o-mini", "context_window": 128000},
        ]
    }
    assert model_binding_by_id({"spec": spec}, "summarizer")["model"] == "gpt-4o-mini"
    model, source = resolve_model_ref({"spec": spec}, "summarizer", engine_model="gpt-4o")
    assert model == "gpt-4o-mini"
    assert source == "spec.models[id=summarizer]"
    model, source = resolve_model_ref({"spec": spec}, None, engine_model="gpt-4o")
    assert model == "gpt-4o"
    assert source == "agent"
    model, source = resolve_model_ref({"spec": spec}, "haiku-3", engine_model="gpt-4o")
    assert model == "haiku-3"
    assert source == "override"
    model, source = resolve_model_ref({"spec": spec}, "gpt-4o", engine_model="gpt-4o")
    assert (model, source) == ("gpt-4o", "agent")


def test_explicit_model_window_and_completion() -> None:
    manifest = {
        "spec": {
            "models": [{"model": "haiku", "context_window": 200000, "max_tokens": 4096}],
        }
    }
    assert derived_trimmer_params(manifest) == (200000, 4096)
    assert history_token_budget(manifest) == 200000 - 4096 - 20000


def test_effective_model_window_is_used_for_auto_budget() -> None:
    manifest = {
        "spec": {
            "models": [{"id": "main", "model": "any", "max_tokens": 12000}],
        }
    }
    assert (
        history_token_budget(
            manifest,
            resolved_model="vertex_ai/gemini-2.5-flash",
        )
        == 1048576 - 12000 - 104858
    )


def test_explicit_window_override_wins_over_resolved_model() -> None:
    manifest = {"spec": {"models": [{"model": "any", "max_tokens": 12000}]}}
    assert (
        history_token_budget(
            manifest,
            resolved_model="vertex_ai/gemini-2.5-flash",
            context_window_override=256000,
        )
        == 256000 - 12000 - 25600
    )


def test_auto_summary_trigger_and_hysteresis_stay_inside_hard_budget() -> None:
    manifest = {
        "spec": {
            "models": [{"id": "main", "model": "any", "max_tokens": 12000}],
            "context_manager": {"type": "summarising"},
        }
    }
    hard_budget = (
        history_token_budget(
            manifest,
            resolved_model="vertex_ai/gemini-2.5-flash",
        )
        - 20000
    )
    trigger = context_manager_history_budget_hint(
        manifest,
        resolved_model="vertex_ai/gemini-2.5-flash",
        fixed_context_tokens=20000,
    )
    assert trigger == int(hard_budget / 1.1)
    assert int(trigger * 1.1) <= hard_budget


def test_explicit_summary_threshold_cannot_exceed_hard_budget() -> None:
    manifest = {
        "spec": {
            "models": [{"model": "gpt-4o", "context_window": 8000, "max_tokens": 500}],
            "context_manager": {"type": "summarising", "params": {"summary_threshold": 100000}},
        }
    }
    hard_budget = history_token_budget(manifest)
    assert context_manager_history_budget_hint(manifest) == int(hard_budget / 1.1)


def test_unknown_model_uses_fallback_with_warning(caplog) -> None:
    manifest = {"spec": {"models": [{"id": "main", "model": "custom-unlisted-model"}]}}
    assert model_context_window(manifest) == 128000
    assert "using fallback 128000" in caplog.text


def test_explicit_trimmer_wins() -> None:
    manifest = {
        "spec": {
            "models": [{"model": "gpt-4o", "context_window": 128000, "max_tokens": 2000}],
            "context_manager": {
                "type": "summarising",
                "params": {"trimmer": {"max_tokens": 12000, "reserve_tokens": 512}},
            },
        }
    }
    assert assembly_trimmer_params(manifest) == (12000, 512 + 1200)
    assert context_manager_history_budget_hint(manifest) == int((12000 - 512 - 1200) / 1.1)


def test_fill_context_manager_defaults_emits_sota_summarising() -> None:
    spec = {"models": [{"model": "gpt-4o", "max_tokens": 1800}]}
    fill_context_manager_defaults(spec)
    cm = spec["context_manager"]
    assert cm["type"] == "summarising"
    assert cm["params"]["keep_turns"] == 10
    assert cm["params"]["hysteresis_ratio"] == 0.1
    assert "context_window" not in spec["models"][0]
    assert cm["params"]["trimmer"] == {"reserve_tokens": 1800, "safety_margin_ratio": 0.1}
    assert "summary_threshold" not in cm["params"]
    assert cm["params"]["summarizer"] == "llm"


def test_fill_preserves_explicit_keep_turns_and_sliding_window() -> None:
    spec = {
        "models": [{"model": "gpt-4o", "context_window": 8000, "max_tokens": 500}],
        "context_manager": {"type": "sliding-window", "params": {"keep_turns": 3}},
    }
    fill_context_manager_defaults(spec)
    params = spec["context_manager"]["params"]
    assert params["keep_turns"] == 3
    assert params["window_size"] == 3
    assert "summary_threshold" not in params
    assert params["trimmer"]["max_tokens"] == 8000
    assert params["trimmer"]["reserve_tokens"] == 500
    assert params["trimmer"]["safety_margin_ratio"] == 0.1


def test_history_budget_uses_primary_model_not_summarizer() -> None:
    """Thresholds come from the agent (main) window, even if a cheaper
    summarizer model is declared with a different window."""
    manifest = {
        "spec": {
            "models": [
                {"id": "main", "model": "gpt-4o", "context_window": 128000, "max_tokens": 2000},
                {
                    "id": "summarizer",
                    "model": "gpt-4o-mini",
                    "context_window": 8000,
                    "max_tokens": 500,
                },
            ]
        }
    }
    assert history_token_budget(manifest) == 128000 - 2000 - 12800


def test_fill_preserves_explicit_summarizer_model() -> None:
    spec = {
        "models": [{"model": "gpt-4o", "max_tokens": 2000}],
        "context_manager": {
            "type": "summarising",
            "params": {
                "summarizer": {"type": "llm", "params": {"model": "gpt-4o-mini"}},
            },
        },
    }
    fill_context_manager_defaults(spec)
    params = spec["context_manager"]["params"]
    assert params["summarizer"] == {
        "type": "llm",
        "params": {"model": "gpt-4o-mini"},
    }
    assert "summary_threshold" not in params


def test_resolve_model_ref_empty_engine() -> None:
    from mas.runtime.spec.model_ref import resolve_model_ref

    model, source = resolve_model_ref({}, None, engine_model=None)
    assert (model, source) == (None, "agent")
