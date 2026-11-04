#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Dict

from mas.lab.components.common.hooks import HookBus


def emit(feed_path: str, event: Dict[str, Any], source: str) -> None:
    bus = HookBus.from_env(default_hooks="ui", source=source)
    bus.emit(event)
