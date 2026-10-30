#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging

import pytest
from mas.ctl.env import env_model_override


@pytest.fixture(autouse=True)
def _clear_warned_overrides(monkeypatch) -> None:
    monkeypatch.setattr("mas.ctl.env._warned_overrides", set())


def test_env_model_override_reads_mas_ctl_model(monkeypatch, caplog) -> None:
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    with caplog.at_level(logging.WARNING, logger="mas.ctl.env"):
        assert env_model_override() == "bedrock/claude-haiku"
    assert "MAS_CTL_MODEL=bedrock/claude-haiku" in caplog.text


def test_env_model_override_falls_back_to_mas_llm_model(monkeypatch) -> None:
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.setenv("MAS_LLM_MODEL", "azure/gpt-4o")
    assert env_model_override() == "azure/gpt-4o"


def test_mas_ctl_model_wins_over_mas_llm_model(monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "bedrock/claude-haiku")
    monkeypatch.setenv("MAS_LLM_MODEL", "azure/gpt-4o")
    assert env_model_override() == "bedrock/claude-haiku"


def test_blank_env_model_override_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("MAS_CTL_MODEL", "  ")
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    assert env_model_override() is None


def test_missing_env_model_override_is_none(monkeypatch) -> None:
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    assert env_model_override() is None
