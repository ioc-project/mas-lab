#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from fake_openai import FakeOpenAI, text
from mas.library.eval.metrics.llm_json import complete_json, extract_json_object


def test_extract_json_object_from_fence() -> None:
    parsed = extract_json_object('```json\n{"ok": true}\n```')
    assert parsed == {"ok": True}


def test_complete_json_retries_malformed(monkeypatch) -> None:
    import mas.library.eval.mce.runner as runner
    from mas.runtime.secrets import SecretRef
    from secret_fakes import bound_secrets

    replies = iter(["not-json", json.dumps({"label": "ok"})])
    with FakeOpenAI(lambda _body: text(next(replies))) as fake, bound_secrets({"judge": "test-key"}):
        service = runner._JudgeService(model="test-model", base_url=fake.url, api_key=SecretRef(name="judge"))
        monkeypatch.setattr(runner, "_service", service)
        parsed = complete_json([{"role": "user", "content": "return json"}], retries=2)
    assert parsed == {"label": "ok"}
    assert len(fake.bodies) == 2
