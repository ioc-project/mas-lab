#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Loopback hosts."""

from __future__ import annotations

import ipaddress


def is_loopback_host(host: str) -> bool:
    """True for ``localhost``, an address in ``127.0.0.0/8``, or ``::1`` (bracketed or not, never IPv4-mapped)."""
    name = str(host or "").lower().removeprefix("[").removesuffix("]")
    if name == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback and getattr(address, "ipv4_mapped", None) is None
