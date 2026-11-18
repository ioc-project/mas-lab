#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The loopback model server fails the test when its script fails, not only the client."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest
from fake_openai import FakeOpenAI, text


def _post(fake: FakeOpenAI) -> bytes:
    request = urllib.request.Request(fake.url + "/chat/completions", data=b"{}", method="POST")
    with urllib.request.urlopen(request) as response:
        return response.read()


def test_a_script_assertion_fails_the_test() -> None:
    def script(_body: dict) -> dict:
        raise AssertionError("script saw a bad request")

    with pytest.raises(AssertionError, match="bad request"):
        with FakeOpenAI(script) as fake:
            with pytest.raises(urllib.error.HTTPError):
                _post(fake)


def test_a_working_script_answers_and_records_the_request() -> None:
    with FakeOpenAI(lambda _body: text("hello")) as fake:
        assert b"hello" in _post(fake)
    assert len(fake.bodies) == 1 and not fake.errors
