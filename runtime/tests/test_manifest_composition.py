#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for manifest composition helpers."""

from mas.ctl.overlay import apply_merge_patch


def test_apply_merge_patch_deletes_keys_with_none() -> None:
    target = {"spec": {"telemetry": {"path": "a.jsonl", "backend": "otel"}}}
    patch = {"spec": {"telemetry": {"backend": None}}}

    result = apply_merge_patch(target, patch)

    assert result == {"spec": {"telemetry": {"path": "a.jsonl"}}}
