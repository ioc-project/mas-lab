#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Deployment runtime_id resolution for mas-lab ctl."""

from pathlib import Path

import pytest
from mas.ctl.deployment import resolve_runtime_id_for_run
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID


def test_resolve_default_runtime_from_tutorial_deployment():
    root = Path(__file__).resolve().parents[2]
    t01 = root / "docs" / "tutorials" / "01-building-an-agent"
    if not t01.is_dir():
        pytest.skip("tutorial bundle not present")
    rid = resolve_runtime_id_for_run(manifest_dir=t01)
    assert rid == DEFAULT_RUNTIME_ID


def test_cli_override_wins():
    root = Path(__file__).resolve().parents[2]
    t01 = root / "docs" / "tutorials" / "01-building-an-agent"
    if not t01.is_dir():
        pytest.skip("tutorial bundle not present")
    rid = resolve_runtime_id_for_run(manifest_dir=t01, cli_override=DEFAULT_RUNTIME_ID)
    assert rid == DEFAULT_RUNTIME_ID


def test_unknown_runtime_rejected():
    from mas.ctl.registry.catalog import UnknownComponentError

    with pytest.raises(UnknownComponentError):
        resolve_runtime_id_for_run(
            manifest_dir=Path("/tmp"),
            cli_override="python-v1",
        )
