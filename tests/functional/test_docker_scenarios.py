#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Docker tutorial scenario steps (need a Docker daemon and the built ``cli`` image).

Build the image first: ``docker compose --profile tools -f docker/compose.yaml build cli``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_TUTORIALS = Path(__file__).resolve().parents[1] / "tutorials"
sys.path.insert(0, str(_TUTORIALS))

import test_scenario_commands as scenarios  # noqa: E402

pytestmark = pytest.mark.functional

_DOCKER_COMPOSE_FILE = scenarios.REPO_ROOT / "docker" / "compose.yaml"

_DOCKER_AVAILABLE: bool | None = None


def _docker_available() -> bool:
    """Return True when the Docker daemon accepts commands."""
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is None:
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            _DOCKER_AVAILABLE = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


_DOCKER_CLI_IMAGE_BUILT: bool | None = None


def _docker_cli_image_built() -> bool:
    """Return True only when the cli service image is already present locally.

    ``docker compose run --no-deps cli`` refuses to build on-demand, so if the
    image is absent the command hangs or fails within the 60-second test timeout.
    The image is built by ``task docker-build`` or the ``docker-build`` scenario
    step (timeout_s: 300) — not by ``task ci``.
    """
    global _DOCKER_CLI_IMAGE_BUILT
    if _DOCKER_CLI_IMAGE_BUILT is None:
        if not _docker_available():
            _DOCKER_CLI_IMAGE_BUILT = False
        else:
            try:
                result = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--profile",
                        "tools",
                        "-f",
                        str(_DOCKER_COMPOSE_FILE),
                        "images",
                        "--quiet",
                        "cli",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                _DOCKER_CLI_IMAGE_BUILT = bool(result.stdout.strip())
            except (OSError, subprocess.TimeoutExpired):
                _DOCKER_CLI_IMAGE_BUILT = False
    return _DOCKER_CLI_IMAGE_BUILT


@pytest.mark.parametrize(
    "case", scenarios.DOCKER_CASES, ids=[scenarios.scenario_case_id(c) for c in scenarios.DOCKER_CASES]
)
def test_docker_scenario_command(case):
    assert _docker_available(), "Docker daemon not available"
    assert _docker_cli_image_built(), (
        "Docker cli image not built: docker compose --profile tools -f docker/compose.yaml build cli"
    )
    scenarios.run_scenario_case(case)
