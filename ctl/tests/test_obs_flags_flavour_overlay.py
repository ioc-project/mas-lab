#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``--events*`` is an agent/MAS shortcut; leftover Flavour observability is unread."""

from __future__ import annotations

from mas.ctl.cli.obs_flags import resolve_observability_config


def test_events_true_on_a_silent_agent_selects_native() -> None:
    cfg = resolve_observability_config(
        events=True,
        events_file=None,
        events_stdout=False,
        events_format=None,
        manifest={"spec": {}},
        flavour_spec={"observability": ["otel"]},
    )
    assert cfg.plugins == ["native"]
    assert cfg.enabled is True


def test_events_false_turns_off_agent_plugins() -> None:
    cfg = resolve_observability_config(
        events=False,
        events_file=None,
        events_stdout=False,
        events_format=None,
        manifest={"spec": {"observability": ["native"]}},
        flavour_spec={"observability": ["native"]},
    )
    assert cfg.plugins == []
    assert cfg.enabled is False


def test_events_file_sets_native_path() -> None:
    cfg = resolve_observability_config(
        events=None,
        events_file="traces/custom.jsonl",
        events_stdout=False,
        events_format=None,
        manifest={"spec": {}},
        flavour_spec={"observability": ["native"]},
    )
    assert cfg.events_file == "traces/custom.jsonl"
    assert cfg.plugins == ["native"]


def test_events_true_with_a_file_keeps_leftover_agent_plugins() -> None:
    cfg = resolve_observability_config(
        events=True,
        events_file="traces/custom.jsonl",
        events_stdout=False,
        events_format=None,
        manifest={"spec": {"observability": ["otel"]}},
        flavour_spec={"observability": ["native"]},
    )
    assert cfg.plugins == ["otel", "native"]
    assert cfg.events_file == "traces/custom.jsonl"


def test_events_format_replaces_agent_plugins() -> None:
    cfg = resolve_observability_config(
        events=None,
        events_file=None,
        events_stdout=False,
        events_format="native",
        manifest={"spec": {"observability": ["otel"]}},
        flavour_spec={"observability": ["otel"]},
    )
    assert cfg.plugins == ["native"]


def test_agent_without_events_records_native() -> None:
    cfg = resolve_observability_config(
        events=None,
        events_file=None,
        events_stdout=False,
        events_format=None,
        manifest={"spec": {}},
        flavour_spec={"observability": [{"native": {"path": "traces/from-flavour.jsonl"}}]},
    )
    assert cfg.plugins == ["native"]
    assert cfg.enabled is True
    assert cfg.events_file is None
