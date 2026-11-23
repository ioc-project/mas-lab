#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Headers revealed for one request: a SecretRef only over https or to loopback."""

from __future__ import annotations

import pytest
from mas.runtime.secrets import SecretRef
from mas.runtime.secrets.errors import SecretMissing
from mas.runtime.secrets.transport import PlainHttpRefused, reveal_headers_for
from secret_fakes import bound_secrets

AUTH = {"Authorization": {"secret": {"name": "peer", "prefix": "Bearer "}}, "Accept": "json"}


def test_a_secret_header_is_revealed_for_https_and_loopback() -> None:
    with bound_secrets({"peer": "tok"}):
        for url in ("https://peer.example/mcp", "http://127.0.0.1:9/mcp"):
            assert reveal_headers_for(url, AUTH, what="secret headers") == {
                "Authorization": "Bearer tok",
                "Accept": "json",
            }


def test_plain_http_to_a_remote_host_is_refused_before_any_reveal() -> None:
    with bound_secrets({"peer": "tok"}), pytest.raises(PlainHttpRefused, match="secret headers"):
        reveal_headers_for("http://peer.example/mcp", AUTH, what="secret headers")


def test_plain_headers_go_anywhere() -> None:
    assert reveal_headers_for("http://peer.example/", {"Accept": "json"}, what="x") == {"Accept": "json"}
    assert reveal_headers_for("http://peer.example/", None, what="x") == {}


def test_reveal_or_none_is_none_only_for_an_optional_missing_ref() -> None:
    with bound_secrets({}) as resolver:
        assert resolver.reveal_or_none(SecretRef(name="absent", optional=True)) is None
        with pytest.raises(SecretMissing):
            resolver.reveal_or_none(SecretRef(name="absent"))
    with bound_secrets({"here": "v"}) as resolver:
        assert resolver.reveal_or_none(SecretRef(name="here", optional=True)) == "v"
