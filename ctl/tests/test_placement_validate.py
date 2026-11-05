#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tests for compose-time placement strategy validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.compose.placement_validate import validate_placement_strategy
from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.deployment.runtime_id import DEFAULT_RUNTIME_ID


def test_validate_local_inproc_ok():
    validate_placement_strategy("local-inproc")


def test_validate_docker_is_not_available():
    with pytest.raises(RuntimeError, match="not available in mas-lab OSS"):
        validate_placement_strategy("docker")


def test_validate_local_multiprocess_is_not_available():
    with pytest.raises(RuntimeError, match="local-inproc"):
        validate_placement_strategy("local-multiprocess")


def test_validate_unknown_strategy_raises():
    with pytest.raises(RuntimeError, match="unknown placement strategy"):
        validate_placement_strategy("bare-metal")


def test_compose_run_rejects_docker_strategy_at_compose_time(tmp_path: Path):
    agent = tmp_path / "agent.yaml"
    agent.write_text(
        """apiVersion: mas/v1
kind: Agent
metadata:
  name: solo
spec:
  description: test
  models:
    - model: gpt-4o-mini
""",
        encoding="utf-8",
    )
    mas_path = tmp_path / "mas.yaml"
    mas_path.write_text(
        """apiVersion: mas/v1
kind: MAS
metadata:
  name: docker-fixture
spec:
  agency:
    agents:
      - id: solo
        ref: agent.yaml
""",
        encoding="utf-8",
    )
    dep_path = tmp_path / "deployment.yaml"
    dep_path.write_text(
        f"""apiVersion: deployment/v1
kind: Deployment
metadata:
  name: docker-dep
spec:
  strategy: docker
  runtime_id: {DEFAULT_RUNTIME_ID}
""",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="mas-lab OSS"):
        compose_run(
            ComposeRequest(
                manifest=mas_path,
                deployment_path=dep_path,
                validate=False,
                infra_refs=["standard:openai"],
            )
        )


def test_get_placement_backend_clear_error_for_multiprocess():
    from mas.ctl.compose.placement_registry import get_placement_backend

    with pytest.raises(RuntimeError, match="not available in mas-lab OSS"):
        get_placement_backend("local-multiprocess")
