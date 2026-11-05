#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for in-process agent chat helpers."""

from __future__ import annotations

from mas.lab.controller.agent_chat import format_error


def test_format_error_extracts_budget_message():
    raw = (
        "Error code: 400 - {'error': {'message': "
        "'ExceededBudget: User=user@example.com over budget. "
        "Spend=501.14, Budget=500.0', 'type': 'budget_exceeded'}}"
    )
    short, full = format_error(raw)
    assert "ExceededBudget" in short
    assert "over budget" in short
    assert full == raw


def test_format_error_empty():
    short, full = format_error("")
    assert short == "Agent execution failed."
    assert full == ""


def test_format_error_truncates_long_message():
    raw = "x" * 300
    short, full = format_error(raw)
    assert len(short) == 240
    assert short.endswith("...")
    assert full == raw
