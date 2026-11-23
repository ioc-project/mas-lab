#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""TLS verification choice and the endpoint url check."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from mas.library.standard.lib import tls
from mas.library.standard.lib.comm.endpoint import AgentCommError, split_url


def test_verification_off_by_env_or_proxy_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tls.resolve_ssl_verify({"verify_ssl": False}) is False
    monkeypatch.setenv("MAS_LLM_VERIFY_SSL", "off")
    assert tls.resolve_ssl_verify() is False


def test_a_configured_ca_bundle_wins_without_truststore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tls, "_system_ssl_context", lambda: None)
    assert tls.resolve_ssl_verify({"ca_bundle": "/some/ca.pem"}) == "/some/ca.pem"


def test_a_configured_ca_bundle_is_loaded_into_the_system_context(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []

    class Ctx:
        def load_verify_locations(self, cafile: str) -> None:
            loaded.append(cafile)

    ctx = Ctx()
    monkeypatch.setattr(tls, "_system_ssl_context", lambda: ctx)
    assert tls.resolve_ssl_verify({"ca_bundle": "/some/ca.pem"}) is ctx
    assert loaded == ["/some/ca.pem"]
    assert tls.resolve_ssl_verify() is ctx


def test_the_ca_bundle_comes_from_the_environment_then_certifi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tls, "_system_ssl_context", lambda: None)
    pem = tmp_path / "ca.pem"
    pem.write_text("pem")
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    assert tls.resolve_ssl_verify() == str(pem)
    monkeypatch.delenv("SSL_CERT_FILE")
    bundle = tls.resolve_ssl_verify()
    assert isinstance(bundle, str) and Path(bundle).is_file()


def test_without_any_bundle_httpx_defaults_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tls, "_system_ssl_context", lambda: None)
    monkeypatch.setattr(tls, "_default_ca_bundle", lambda: None)
    assert tls.resolve_ssl_verify() is True


def test_system_context_is_an_ssl_context_when_truststore_is_installed() -> None:
    ctx = tls._system_ssl_context()
    assert ctx is None or isinstance(ctx, ssl.SSLContext)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://u:p@127.0.0.1/", "must not carry credentials"),
        ("ftp://host/", "is not supported"),
        ("http://", "missing a host"),
        ("http://gw.example/", "only allowed on a loopback host"),
    ],
)
def test_endpoint_urls_are_refused(url: str, message: str) -> None:
    with pytest.raises(AgentCommError, match=message):
        split_url(url, what="peer")
