#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Conformance checks any call layer can import: bind, start bottom-first, stop, capabilities, inner errors."""

from __future__ import annotations

from typing import Any, Callable

from mas.runtime.layers.lifecycle import Lifecycle


class RecordingInner(Lifecycle):
    """Inner call used by the suite. Tracks start/stop and can raise."""

    def __init__(self, log: list[str], *, fail: bool = False) -> None:
        self.log = log
        self.fail = fail

    def bind(self, inner: object) -> None:
        return None

    def start(self) -> None:
        self.log.append("inner-start")

    def stop(self) -> None:
        self.log.append("inner-stop")

    def capabilities(self) -> dict[str, bool]:
        return {"inner": True}

    def invoke(self, request: Any) -> Any:
        if self.fail:
            raise RuntimeError("inner failed")
        return request

    async def ainvoke(self, request: Any) -> Any:
        return self.invoke(request)


def assert_layer_conformance(make_layer: Callable[..., Any], request: Any) -> None:
    """Bind, start inner first, stop, merge capabilities, propagate inner exceptions."""
    log: list[str] = []
    inner = RecordingInner(log)
    outer = make_layer()
    outer.bind(inner)
    outer.start()
    if log[:1] != ["inner-start"]:
        raise AssertionError(f"start must run the inner layer first, got {log}")
    merged = outer.capabilities()
    if not isinstance(merged, dict):
        raise AssertionError("capabilities must return a dict")
    if not merged.get("inner"):
        raise AssertionError("capabilities must merge the inner layer")
    boom = RecordingInner(log, fail=True)
    failing = make_layer()
    failing.bind(boom)
    try:
        failing.invoke(request)
    except RuntimeError as exc:
        if "inner failed" not in str(exc):
            raise AssertionError("exception in inner must reach the outer layer") from exc
    else:
        raise AssertionError("exception in inner must reach the outer layer")
    outer.stop()
    if "inner-stop" not in log:
        raise AssertionError("stop must reach the inner layer")
