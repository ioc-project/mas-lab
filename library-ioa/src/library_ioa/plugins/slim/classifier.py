#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SLIM has no SDK spans. Classifier is hooks-only (empty scopes)."""

from __future__ import annotations

from mas.runtime.transport.classifier import AliasTable, Decision, Drop, SpanView

from library_ioa.plugins.slim.aliases import ALIASES

SUPPORTED_RANGE = ">=2.2,<3"


class SlimTransportClassifier:
    protocol = "slim"
    sdk_distribution = "slim-bindings"

    def scope_names(self) -> tuple[str, ...]:
        return ()

    def supported_sdk_range(self) -> str:
        return SUPPORTED_RANGE

    def mapped_span_names(self) -> tuple[str, ...]:
        return ()

    def aliases(self) -> AliasTable:
        return ALIASES

    def classify_start(self, span: SpanView) -> Decision:
        del span
        return Drop()

    def classify_end(self, span: SpanView) -> Decision:
        del span
        return Drop()


__all__ = ["SlimTransportClassifier", "SUPPORTED_RANGE"]
