#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``hostenv.install`` keeps declared keys only, from a snapshot and from a live mapping alike."""

from __future__ import annotations

import os

import pytest
from mas.runtime import hostenv


@pytest.mark.parametrize("live", [True, False], ids=["live", "snapshot"])
def test_install_hides_undeclared_and_store_keys(monkeypatch: pytest.MonkeyPatch, live: bool) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-canary")
    monkeypatch.setenv("MAS_SECRET_OPENAI", "sk-store-canary")
    monkeypatch.setenv("MAS_HOME", "/m")
    hostenv.install(os.environ if live else dict(os.environ))
    assert hostenv.get("OPENAI_API_KEY") is None and hostenv.get("MAS_SECRET_OPENAI") is None
    assert hostenv.get("MAS_HOME") == "/m"
    snap = hostenv.snapshot()
    assert "sk-live-canary" not in repr(snap) and "sk-store-canary" not in repr(snap)
    assert hostenv.private("MAS_SECRET_OPENAI") == "sk-store-canary"
    with pytest.raises(KeyError):
        hostenv.private("OPENAI_API_KEY")
