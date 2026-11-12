#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""ctl reads MAS_LLM_HTTP_* as last-resort interactive overrides."""

from __future__ import annotations

from mas.ctl.session.llm_retry_env import llm_http_retry_overrides


def test_unset_env_is_an_empty_overlay(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_HTTP_RETRIES", raising=False)
    monkeypatch.delenv("MAS_LLM_HTTP_RETRY_BACKOFF", raising=False)
    assert llm_http_retry_overrides() == {}


def test_retries_are_extra_attempts(monkeypatch) -> None:
    monkeypatch.setenv("MAS_LLM_HTTP_RETRIES", "2")
    monkeypatch.delenv("MAS_LLM_HTTP_RETRY_BACKOFF", raising=False)
    assert llm_http_retry_overrides() == {"max_attempts": 3}


def test_backoff_disables_jitter(monkeypatch) -> None:
    monkeypatch.delenv("MAS_LLM_HTTP_RETRIES", raising=False)
    monkeypatch.setenv("MAS_LLM_HTTP_RETRY_BACKOFF", "0.25")
    assert llm_http_retry_overrides() == {"backoff_s": 0.25, "jitter": False}
