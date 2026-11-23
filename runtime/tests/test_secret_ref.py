#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SecretRef parsing: short form, long form, prefix, names; listed HTTP auth headers."""

from __future__ import annotations

import pytest
from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.ref import SecretRef, check_http_header_secrets, is_ref, iter_secret_refs, parse_secret_ref


def test_short_and_long_form() -> None:
    short = parse_secret_ref({"secret": "openai"})
    assert short == SecretRef(name="openai", key="value") and str(short) == "secret:openai/value"
    assert short.to_manifest() == {"secret": "openai"}
    long = parse_secret_ref({"secret": {"name": "mcp", "key": "bearer", "optional": True, "prefix": "Bearer "}})
    assert (long.name, long.key, long.optional, long.prefix) == ("mcp", "bearer", True, "Bearer ")
    assert long.to_manifest()["secret"]["prefix"] == "Bearer "


@pytest.mark.parametrize(
    "raw",
    [
        "sk-live",
        {"secret": "a", "url": "u"},
        {"secret": {"key": "k"}},
        {"secret": {"name": "a", "bogus": 1}},
        {"secret": {"name": "a", "optional": "no"}},
        {"secret": 5},
    ],
)
def test_a_malformed_ref_is_refused(raw: object) -> None:
    with pytest.raises(SecretRefError):
        parse_secret_ref(raw)


def test_iter_and_is_ref() -> None:
    raw = {"api_key": {"secret": "openai"}, "headers": [{"Authorization": SecretRef("mcp")}], "url": "https://e/v1"}
    assert {r.name for r in iter_secret_refs(raw)} == {"openai", "mcp"}
    assert is_ref(SecretRef("a")) and is_ref({"secret": "a"})
    row = {"protocol": "slim", "url": "http://127.0.0.1:9", "secret": {"secret": "slim"}}
    assert not is_ref(row) and not is_ref("a")  # an application row with a secret field is not itself a ref


@pytest.mark.parametrize("name", ["Authorization", " authorization ", "X-API-KEY", "cookie", "Set-Cookie", "api-key"])
@pytest.mark.parametrize("value", ["Bearer tok", ["Bearer tok"], 123, None, {"value": "tok"}])
def test_a_listed_header_holds_only_a_secret_ref(name: str, value: object) -> None:
    with pytest.raises(SecretRefError, match="SecretRef"):
        check_http_header_secrets({name: value})


def test_a_listed_header_accepts_a_ref_and_other_headers_accept_strings() -> None:
    check_http_header_secrets({" Authorization ": {"secret": "mcp"}, "Accept": "application/json"})
    check_http_header_secrets({"Authorization": SecretRef(name="mcp")})
    check_http_header_secrets(None)


@pytest.mark.parametrize("prefix", ["Bearer\n", "Bearer\r\nX-Evil: 1 ", "Bearer sk-LIVE-abc ", "Bearer", "\x00 ", 5])
def test_a_prefix_is_one_scheme_word_and_a_space(prefix: object) -> None:
    with pytest.raises(SecretRefError) as caught:
        parse_secret_ref({"secret": {"name": "a", "prefix": prefix}})
    assert "sk-LIVE" not in str(caught.value)
    with pytest.raises(SecretRefError):
        SecretRef(name="a", prefix=prefix)  # type: ignore[arg-type]
    assert all(SecretRef(name="a", prefix=p).prefix == p for p in ("", "Bearer ", "Basic ", "DPoP "))


@pytest.mark.parametrize("bad", ["../x", "OpenAI", " openai", "a/b", ".hidden", "x" * 129, "openai\n"])
def test_the_constructor_refuses_what_the_schema_refuses(bad: str) -> None:
    for build in (lambda: SecretRef(name=bad), lambda: SecretRef(name="ok", key=bad)):
        with pytest.raises(SecretRefError):
            build()
    with pytest.raises(SecretRefError):
        parse_secret_ref({"secret": bad})
    with pytest.raises(SecretRefError, match="optional"):
        SecretRef(name="openai", optional="no")  # type: ignore[arg-type]
