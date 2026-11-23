#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One URL credential check, one origin rule and one unsafe-character check for every client that sends a secret."""

from __future__ import annotations

import pytest
from mas.runtime import envkeys, hostenv
from mas.runtime.secrets import SecretRefError
from mas.runtime.secrets.url import check_url_has_no_credentials, has_unsafe_chars, origin, origin_text, same_origin

CANARY = "S3CR3T-url-canary"


@pytest.mark.parametrize(
    "url",
    [
        f"https://user:{CANARY}@api.example.com/v1",
        f"//u:{CANARY}@h/",
        f"http://u%3A{CANARY}%40@h/",
        f"https://user:{CANARY}\\@api.example.com/v1",
        f"http://u\n:{CANARY}@h",
        f"neo4j:{CANARY}@graph:7687",
        f"https://h/x?API_KEY={CANARY}",
        f"https://h/x?q=1;access_token={CANARY}",
        f"https://h/cb#a=1&sig={CANARY}",
        f"https://h/x?Password={CANARY}",
    ],
)
def test_a_credential_in_a_url_is_refused_without_quoting_it(url: str) -> None:
    with pytest.raises(SecretRefError) as caught:
        check_url_has_no_credentials(url, field="spec.url")
    assert str(caught.value).startswith("spec.url: ") and CANARY not in str(caught.value)
    for name in ("HTTPS_PROXY", "OPENAI_BASE_URL"):
        with pytest.raises(hostenv.HostEnvironmentError, match=name):
            hostenv.refuse_credentialed_urls({name: url})


@pytest.mark.parametrize(
    "value",
    [
        "git@github.com:org/repo.git",
        "repo/image:tag@sha256:abcdef",
        "user@example.com",
        "http://host/a;b=c",
        "https://h/p@x?tokens_used=3&page=2",
        "http://[::1]:8080/x",
        "https://docs.example is the site\nsee there",
        42,
    ],
)
def test_strings_without_a_credential_pass(value: object) -> None:
    check_url_has_no_credentials(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "expected", "shown"),
    [
        ("https://u:S3@API.example.com:8443/v?q", ("https", "api.example.com", 8443), "https://api.example.com:8443"),
        ("http://h", ("http", "h", 80), "http://h"),
        ("ftp://h", ("ftp", "h", 0), "ftp://h"),  # no services-database lookup: only the fixed map
        ("http://[::1]:9/x", ("http", "::1", 9), "http://[::1]:9"),
        ("", ("", "", 0), "(no url)"),
    ],
)
def test_origin_and_origin_text(url: str, expected: tuple[str, str, int], shown: str) -> None:
    assert origin(url) == expected
    assert origin_text(url) == shown


@pytest.mark.parametrize(
    ("a", "b", "upgrade", "same"),
    [
        ("https://h/a", "https://H:443/b", False, True),
        ("http://h", "https://h", False, False),
        ("http://h", "https://h", True, True),
        ("https://h", "http://h", True, False),  # never a downgrade
        ("", "", False, False),
    ],
)
def test_same_origin(a: str, b: str, upgrade: bool, same: bool) -> None:
    assert same_origin(a, b, upgrade=upgrade) is same


def test_unsafe_chars_and_credential_names() -> None:
    assert all(has_unsafe_chars(u) for u in ("http://a\\b", "http://a b", "http://a\x00b", "http://a\tb"))
    assert not has_unsafe_chars("https://api.example.com/v1?x=1")
    assert all(hostenv.is_credential_name(n) for n in ("OPENAI_API_KEY", "MAS_SECRET_OPENAI", "PIP_INDEX_URL"))
    assert not any(hostenv.is_credential_name(n) for n in ("PATH", "MAS_HOME", "HTTPS_PROXY"))
    assert not [name for name in envkeys.DECLARED_KEYS if hostenv.is_credential_name(name)]
    with pytest.raises(hostenv.OverlayRefused, match="OPENAI_API_KEY"):
        hostenv.set_overlay({"OPENAI_API_KEY": "plain"})
