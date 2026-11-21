#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mas.library.lab.steps.services.service_start import ServiceStartStep
from mas.library.lab.steps.services.service_stop import ServiceStopStep


@pytest.mark.asyncio
async def test_service_start_stop_share_process_manager(tmp_path: Path) -> None:
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "services.yaml").write_text(
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
    (tmp_path / "experiment.yaml").write_text("name: demo\n", encoding="utf-8")
    ctx = SimpleNamespace(
        output_dir=tmp_path,
        pipeline=SimpleNamespace(config_path=tmp_path / "experiment.yaml"),
    )
    start = ServiceStartStep(
        "start",
        {"service": "sleeper", "health_timeout": 0, "keep_running": True},
    )
    stop = ServiceStopStep("stop", {"service": "sleeper"})
    started = await start.execute(ctx)
    assert started.metadata["started"] is True
    stopped = await stop.execute(ctx)
    assert stopped.metadata["stopped"] is True
    missing = await ServiceStartStep("miss", {"service": "nope", "health_timeout": 0}).execute(ctx)
    assert missing.metadata["started"] is False
