#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Single-pass design pattern — ingress governance path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mas.library.standard.plugins.design_patterns.single_pass import SinglePassPlugin
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import DpState, QProduct, RunLedger
from mas.runtime.schema.ingress import EngineIoReturn


def test_single_pass_engine_io_return_uses_apply_engine_io_return():
    plugin = SinglePassPlugin()
    q = QProduct(dp=DpState.AWAITING_INGRESS)
    run = RunLedger()
    event = EngineIoReturn(
        correlation_id=1,
        response_kind="MODEL_TEXT",
        next_step="STOP",
        text="done",
    )
    config = KernelConfig()

    with patch(
        "mas.library.standard.plugins.design_patterns.single_pass.apply_engine_io_return",
        return_value=[MagicMock()],
    ) as apply_mock:
        out = plugin.handle_event(q, run, event, config=config)

    apply_mock.assert_called_once_with(
        q,
        run,
        event,
        config=config,
        evaluate=plugin._evaluate,
    )
    assert out == apply_mock.return_value
