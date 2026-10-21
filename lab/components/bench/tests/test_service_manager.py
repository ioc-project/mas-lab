#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from mas.lab.benchmark.service_manager import ServiceManager


def test_reads_service_bundle_spec(tmp_path: Path) -> None:
    path = tmp_path / "bundle.yaml"
    path.write_text(
        "\n".join(
            [
                "apiVersion: infra/v1",
                "kind: ServiceBundle",
                "metadata: {name: demo}",
                "spec:",
                "  services:",
                "    demo:",
                "      backend: process",
                "      command: [python3, -c, pass]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    mgr = ServiceManager(path)
    assert mgr._services["demo"].backend == "process"
    assert mgr._services["demo"].command == ["python3", "-c", "pass"]


def test_reads_legacy_top_level_services(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        "services: {demo: {backend: process, command: [python3]}}\n",
        encoding="utf-8",
    )
    mgr = ServiceManager(path)
    assert "demo" in mgr._services
