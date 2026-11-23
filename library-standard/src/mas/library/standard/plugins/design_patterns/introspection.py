#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Introspection / reflection pattern — self-critique pass before final answer."""

from __future__ import annotations

from mas.library.standard.plugins.design_patterns.cot import CotPlugin
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.state import QProduct, RunLedger
from mas.runtime.schema.egress import EgressSymbol


class IntrospectionPlugin(CotPlugin):
    """Reflection/introspection — extra LLM critique pass (tutorial ``reflection`` overlay)."""

    plugin_id = "introspection@v1"

    def _evaluate(self, q: QProduct, run: RunLedger, config: KernelConfig) -> list[EgressSymbol]:
        max_pass = max(config.max_cot_pass, 1)
        original = config
        if max_pass < 2:
            from dataclasses import replace

            original = replace(config, max_cot_pass=2)
        return super()._evaluate(q, run, original)


ReflectionPlugin = IntrospectionPlugin
