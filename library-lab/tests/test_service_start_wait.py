#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The health wait probes quickly first: a ready local service must not cost a full second."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.error import URLError

from mas.library.lab.steps.services import service_start


def test_health_wait_backs_off_from_a_short_first_pause(monkeypatch):
    calls = {"n": 0}
    pauses: list[float] = []

    def fake_open(url, timeout):
        calls["n"] += 1
        if calls["n"] < 4:
            raise URLError("not yet")
        return SimpleNamespace(status=200)

    monkeypatch.setattr("mas.lab.benchmark.environment.fixtures.open_url", fake_open)
    monkeypatch.setattr(service_start.time, "sleep", pauses.append)
    assert service_start._wait_for_url("http://127.0.0.1:1/health", 30)
    assert pauses == [0.05, 0.1, 0.2]


def test_health_wait_gives_up_at_the_deadline(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(service_start.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(service_start.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    def refuse(url, timeout):
        raise URLError("down")

    monkeypatch.setattr("mas.lab.benchmark.environment.fixtures.open_url", refuse)
    assert service_start._wait_for_url("http://127.0.0.1:1/health", 3) is False
    assert clock["t"] <= 3.0 + 1e-9
