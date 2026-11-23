#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Which hosts are loopback."""

from __future__ import annotations

import pytest
from mas.runtime.secrets import is_loopback_host


@pytest.mark.parametrize("host", ["localhost", "LocalHost", "127.0.0.1", "127.1.2.3", "::1", "[::1]"])
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        "localhost.",
        "localhost.evil.com",
        "evil.localhost",
        "127.0.0.1.evil.com",
        "0.0.0.0",
        "10.0.0.5",
        "::ffff:127.0.0.1",
        "0x7f000001",
        None,
    ],
)
def test_hosts_that_are_not_loopback(host: str) -> None:
    assert is_loopback_host(host) is False
