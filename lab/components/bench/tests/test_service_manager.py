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


def test_process_start_then_stop_via_shared_manager(tmp_path: Path) -> None:
    import time

    from mas.lab.benchmark.service_manager import shared_manager, stop_all_shared

    path = tmp_path / "bundle.yaml"
    path.write_text(
        "\n".join(
            [
                "apiVersion: infra/v1",
                "kind: ServiceBundle",
                "metadata: {name: demo}",
                "spec:",
                "  services:",
                "    sleeper:",
                "      backend: process",
                '      command: [python3, -c, "import time; time.sleep(30)"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    a = shared_manager(path, output_dir=tmp_path)
    assert a.start("sleeper")
    time.sleep(0.05)
    b = shared_manager(path, output_dir=tmp_path)
    assert a is b
    assert b.stop("sleeper")
    stop_all_shared(include_keep_running=True)


def test_reads_legacy_top_level_services(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        "services: {demo: {backend: process, command: [python3]}}\n",
        encoding="utf-8",
    )
    mgr = ServiceManager(path)
    assert "demo" in mgr._services


def test_a_process_service_sees_what_an_earlier_service_exports(tmp_path: Path) -> None:
    import sys
    import time

    from mas.lab.benchmark.service_manager import shared_manager, stop_all_shared

    seen = tmp_path / "seen.txt"
    code = f"import os,time; open({str(seen)!r}, 'w').write(os.environ.get('SVC_ENDPOINT', 'missing')); time.sleep(30)"
    path = tmp_path / "bundle.yaml"
    path.write_text(
        "\n".join(
            [
                "apiVersion: infra/v1",
                "kind: ServiceBundle",
                "metadata: {name: demo}",
                "spec:",
                "  services:",
                "    first:",
                "      backend: process",
                f'      command: [{sys.executable!r}, -c, "import time; time.sleep(30)"]',
                "      env: {SVC_ENDPOINT: 'http://first'}",
                "    second:",
                "      backend: process",
                f"      command: [{sys.executable!r}, -c, {code!r}]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    mgr = shared_manager(path, output_dir=tmp_path, base_env={"PATH": "/usr/bin:/bin"})
    try:
        assert mgr.start("first") and mgr.start("second")
        for _ in range(300):
            if seen.exists() and seen.read_text():
                break
            time.sleep(0.05)
        assert seen.read_text() == "http://first"
    finally:
        stop_all_shared(include_keep_running=True)
