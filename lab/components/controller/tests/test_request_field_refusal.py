#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Every route that keeps a request string (command line, job row, log, file) refuses a secret in it."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from test_fastapi_app import client, demo_lab  # noqa: F401  (fixtures)

CANARY = "sk-S3CR3T-field-canary"
URL = f"https://u:{CANARY}@h.example/m"
LIB = "/api/libraries/demo"
_AGENT = "apiVersion: mas/v1\nkind: Agent\nmetadata:\n  name: t\nspec:\n  llm:\n    model: x\n"

# (path, body): one row per field that reaches a sink.
_CASES = [
    (f"{LIB}/pipeline/run", {"pipeline_yaml": f"{{config: {{api_key: {CANARY}}}}}"}),
    (f"{LIB}/pipeline/run", {"pipeline_yaml": "pipelines/p.yaml", "only": [URL]}),
    (f"{LIB}/benchmark/run", {"experiment_yaml": "e.yaml", "flavour": URL}),
    (f"{LIB}/benchmark/analyze", {"benchmark_id": "b", "experiment_yaml": f"vars:\n  api_key: {CANARY}\n"}),
    (f"{LIB}/benchmark/analyze", {"benchmark_id": URL}),
    (f"{LIB}/benchmark/export", {"benchmark_id": "b", "output": URL}),
    (f"{LIB}/benchmark/import", {"tarball": URL}),
    (f"{LIB}/eval-output", {"events_file": "e.jsonl", "model": URL}),
    (f"{LIB}/experiment/run", {"experiment_yaml": "x", "demo_ui_ingest_url": URL}),
    (f"{LIB}/experiment/run", {"experiment_yaml": "x", "demo_ui_ref": {"api_key": CANARY}}),
    (f"{LIB}/run", {"manifest_yaml": _AGENT, "query": "q", "flavour": URL}),
    (f"{LIB}/run/multi-turn", {"manifest_yaml": _AGENT, "queries": [URL]}),
    (f"{LIB}/run-mas", {"manifest_yaml": _AGENT, "query": "q", "demo_ui_ref": {"token": CANARY}}),
    ("/api/runs", {"scope": "demo", "kind": "agent", "item": "a", "bind": URL}),
    ("/api/scopes", {"name": "s6", "description": f"api_key: {CANARY}"}),
]


@pytest.mark.parametrize(("url", "body"), _CASES, ids=lambda v: str(v)[:40])
def test_request_field_with_a_secret_is_refused(client, demo_lab: Path, caplog, url, body) -> None:  # noqa: F811
    from mas.lab.controller import jobs

    before = set(jobs._jobs)
    with caplog.at_level(logging.DEBUG):
        response = client.post(url, json=body)
    assert response.status_code == 422, (url, response.status_code, response.text[:300])
    assert CANARY not in response.text + caplog.text
    assert set(jobs._jobs) == before
    assert not any(CANARY in p.read_text(errors="replace") for p in demo_lab.rglob("*") if p.is_file())


def test_stream_command_with_a_secret_is_refused() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mas.lab.controller.ui_stream.app import mount_ui_stream

    app = FastAPI()
    mount_ui_stream(app)
    response = TestClient(app).post("/api/commands", json={"verb": "run", "ref": {"api_key": CANARY}})
    assert response.status_code == 422 and CANARY not in response.text


def test_a_missing_experiment_is_not_echoed(client) -> None:  # noqa: F811
    response = client.post(f"{LIB}/experiment/run", json={"experiment_yaml": f"nope-{CANARY[:12]}"})
    assert response.status_code == 404 and CANARY[:12] not in response.text
