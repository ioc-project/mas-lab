#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""_cache_read_enabled/_cache_write_enabled: precedence is CLI override ->
RuntimeEngine cache.enabled (hard kill-switch) -> cache read/write ->
MAS_LLM_CACHE_READ/MAS_LLM_CACHE_WRITE env var -> default false."""

from __future__ import annotations

from mas.ctl.session.engine_factory import _cache_read_enabled, _cache_write_enabled


def _runtime(cache: dict) -> dict:
    return {"cache": cache}


def test_defaults_to_false_with_no_runtime_or_env():
    assert _cache_read_enabled({}) is False
    assert _cache_write_enabled({}) is False


def test_runtime_read_write_fields_are_independent():
    rt = _runtime({"read": False, "write": True})
    assert _cache_read_enabled(rt) is False
    assert _cache_write_enabled(rt) is True


def test_enabled_false_is_a_hard_kill_switch_for_both():
    rt = _runtime({"enabled": False, "read": True, "write": True})
    assert _cache_read_enabled(rt) is False
    assert _cache_write_enabled(rt) is False


def test_env_var_used_when_runtime_silent(monkeypatch):
    monkeypatch.setenv("MAS_LLM_CACHE_READ", "false")
    monkeypatch.setenv("MAS_LLM_CACHE_WRITE", "true")
    assert _cache_read_enabled({}) is False
    assert _cache_write_enabled({}) is True


def test_runtime_field_wins_over_env_var(monkeypatch):
    monkeypatch.setenv("MAS_LLM_CACHE_READ", "false")
    rt = _runtime({"read": True})
    assert _cache_read_enabled(rt) is True


def test_cli_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("MAS_LLM_CACHE_READ", "true")
    rt = _runtime({"enabled": False, "read": True})
    assert _cache_read_enabled(rt, override=False) is False
    assert _cache_write_enabled(rt, override=True) is True
