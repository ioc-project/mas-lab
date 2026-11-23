#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The MCE judge reveals its key per run: after a run nothing in the runner module holds it, the jury
config carries no key, and a rotated secret is used by the next run."""

from __future__ import annotations

import json
from collections.abc import Iterator

import mas.library.eval.mce.runner as runner
import pytest
from fake_openai import FakeOpenAI, text
from mas.runtime.secrets import SecretResolver, set_process_resolver
from pydantic import BaseModel, Field
from secret_fakes import MemorySecretStore

CANARY = "sk-JUDGE-CANARY-4242"


class _Grade(BaseModel):
    metric_score: float = Field(description="score")
    score_reasoning: str = Field(description="why")


@pytest.fixture
def server() -> Iterator[FakeOpenAI]:
    with FakeOpenAI(lambda _body: text(json.dumps({"metric_score": 1, "score_reasoning": "ok"}))) as fake:
        yield fake


@pytest.fixture
def store(monkeypatch, server) -> Iterator[MemorySecretStore]:
    monkeypatch.setattr(runner, "_service", None)
    monkeypatch.setattr(runner, "_jury", None)
    monkeypatch.setattr(runner, "_resolve_infra", lambda model=None: (server.url, {"secret": "judge"}, "judge-model"))
    monkeypatch.setattr(runner, "_resolve_infra_proxy", lambda: (None, None, {}))
    monkeypatch.setattr(runner, "_resolve_infra_generation", lambda: {})
    held = MemorySecretStore({"judge": {"value": CANARY}})
    set_process_resolver(SecretResolver(held, ttl_s=0.0))
    yield held


def _module_holds(text: str) -> bool:
    return text in repr(vars(runner))


def test_the_runner_never_holds_the_key_and_a_rotated_secret_is_used(store, server) -> None:
    runner.install_openai_llm_service(model_override="judge-model")
    with runner.judge_client():
        score, _reason = runner.get_jury().judge("grade it", response_format=_Grade)
    assert score == 1 and not _module_holds(CANARY)
    assert CANARY not in repr(runner._service) and CANARY not in repr(vars(runner.get_jury().llms[0]))
    store.put("judge", "rotated-key")
    runner.get_jury().judge("grade it", response_format=_Grade)
    assert server.authorizations() == [f"Bearer {CANARY}", "Bearer rotated-key"]
    assert not _module_holds("rotated-key")
