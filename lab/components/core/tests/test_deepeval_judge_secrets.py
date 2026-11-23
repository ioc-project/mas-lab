#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The deepeval judge never falls back to deepeval's own client, which reads the process environment."""

from __future__ import annotations

import pytest
from mas.lab.components.evaluation import deepeval_wrapper as dw
from mas.runtime.secrets import SecretDenied, SecretRef, SecretResolver, set_process_resolver
from secret_fakes import PROVIDER_KEY as _KEY
from secret_fakes import MemorySecretStore

runner = pytest.importorskip("mas.library.eval.mce.runner")


class _DenyingStore(MemorySecretStore):
    def get(self, ref: SecretRef):
        raise SecretDenied(ref, "file is group or world readable")


def _no_infra(monkeypatch):
    monkeypatch.setattr(runner, "_resolve_infra", lambda **_: ("", None, "gpt-4o"))


def test_a_denied_secret_stops_the_judge(monkeypatch):
    _no_infra(monkeypatch)
    set_process_resolver(SecretResolver(_DenyingStore()))
    with pytest.raises(SecretDenied):
        dw._resolve_model_arg("gpt-4o")
    if dw.DEEPEVAL_AVAILABLE:  # the metric does not turn the error into a mock score
        with pytest.raises(SecretDenied):
            dw.AnswerRelevancyMetric(model="gpt-4o")


def _completion_transport(seen: list):
    import httpx

    msg = {"role": "assistant", "content": "ok"}
    done = {"id": "x", "object": "chat.completion", "created": 0, "model": "m"}
    done["choices"] = [{"index": 0, "message": msg, "finish_reason": "stop"}]
    return httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200, json=done))


def test_a_judge_without_a_key_sends_no_authorization_even_with_openai_env(monkeypatch):
    # kills: the SDK reading OPENAI_API_KEY / OPENAI_BASE_URL, or a placeholder bearer, for an unset optional key
    import httpx
    from mas.library.standard.lib.openai_client import build_openai_client

    monkeypatch.setenv("OPENAI_API_KEY", _KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/never")
    seen: list = []
    client = build_openai_client(
        "", "http://judge.local/v1", http_client=httpx.Client(transport=_completion_transport(seen))
    )
    client.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
    assert "authorization" not in seen[0].headers
    assert _KEY not in str(seen[0].headers)
    assert str(seen[0].url).startswith("http://judge.local/v1/")


def test_a_judge_error_is_its_status_only():
    # kills: the deepeval metric's ``error`` field carrying ``str(exc)`` of an SDK error (the provider's body)
    import httpx
    import openai

    request = httpx.Request("POST", "http://judge.local/v1/chat/completions")
    body = {"error": {"message": f"echo {_KEY}", "type": _KEY, "code": _KEY}}
    response = httpx.Response(401, json=body, request=request, headers={"x-request-id": _KEY})
    error = openai.AuthenticationError(f"Error code: 401 - {body}", response=response, body=body)

    class _Raises:
        def measure(self, _case):
            raise error

    metric = dw.AnswerRelevancyMetric.__new__(dw.AnswerRelevancyMetric)
    metric.threshold, metric.score, metric.reason, metric._delegate = 0.5, 0.0, "", _Raises()
    out = metric.compute(run_id="r", level="mas", context={"input": "q", "output": "a"})
    assert out["error"] == "AuthenticationError: HTTP 401"
    assert _KEY not in str(out)
