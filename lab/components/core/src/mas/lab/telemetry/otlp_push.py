#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Compatibility shim — OTLP helpers live in ``mas.library.telemetry``."""

from mas.library.telemetry.collector.otlp import (
    convert_file_to_otlp_jsonl as convert_to_jsonl,
)
from mas.library.telemetry.collector.otlp import load_events, push_file

__all__ = ["convert_to_jsonl", "load_events", "push_file"]
