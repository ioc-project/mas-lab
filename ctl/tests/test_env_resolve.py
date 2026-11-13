#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""infra/env_resolve.py — env: indirection resolution and its log levels."""

from __future__ import annotations

import logging

from mas.ctl.infra.env_resolve import resolve_env_string


def test_resolves_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAS_TEST_ENV_RESOLVE_VAR", "resolved-value")
    assert resolve_env_string("env:MAS_TEST_ENV_RESOLVE_VAR") == "resolved-value"


def test_non_env_prefixed_value_passes_through_unchanged() -> None:
    assert resolve_env_string("https://example.com") == "https://example.com"


def test_unset_var_with_default_uses_default_and_logs_debug(monkeypatch, caplog) -> None:
    monkeypatch.delenv("MAS_TEST_ENV_RESOLVE_UNSET", raising=False)
    with caplog.at_level(logging.DEBUG, logger="mas.ctl.infra.env_resolve"):
        value = resolve_env_string("env:MAS_TEST_ENV_RESOLVE_UNSET|fallback-value")
    assert value == "fallback-value"
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any("using manifest default" in r.message for r in caplog.records)


def test_unset_var_with_no_default_returns_empty_without_warning(monkeypatch, caplog) -> None:
    monkeypatch.delenv("MAS_TEST_ENV_RESOLVE_UNSET_NO_DEFAULT", raising=False)
    with caplog.at_level(logging.DEBUG, logger="mas.ctl.infra.env_resolve"):
        value = resolve_env_string("env:MAS_TEST_ENV_RESOLVE_UNSET_NO_DEFAULT")
    assert value == ""
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_env_prefix_with_no_var_name_returns_empty() -> None:
    assert resolve_env_string("env:") == ""


def test_an_address_taken_from_the_environment_is_warned_once(monkeypatch, caplog):
    import logging

    from mas.ctl.infra import env_resolve

    env_resolve._warned_endpoints.clear()
    monkeypatch.setenv("OPENAI_BASE_URL", "http://user:pw@proxy.example:8080/v1")
    with caplog.at_level(logging.WARNING):
        assert env_resolve.resolve_env_string("env:OPENAI_BASE_URL|https://api.openai.com/v1").startswith("http://")
        env_resolve.resolve_env_string("env:OPENAI_BASE_URL|https://api.openai.com/v1")
    warnings = [r for r in caplog.records if "API key" in r.getMessage()]
    assert len(warnings) == 1
    assert "proxy.example:8080" in warnings[0].getMessage()
    assert "pw" not in warnings[0].getMessage()


def test_the_file_fallback_and_secret_names_do_not_warn(monkeypatch, caplog):
    import logging

    from mas.ctl.infra import env_resolve

    env_resolve._warned_endpoints.clear()
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("MCP_AUTH_HEADER", "Bearer x")
    with caplog.at_level(logging.WARNING):
        env_resolve.resolve_env_string("env:OPENAI_BASE_URL|https://api.openai.com/v1")
        env_resolve.resolve_env_string("env:MCP_AUTH_HEADER")
    assert not [r for r in caplog.records if "API key" in r.getMessage()]
