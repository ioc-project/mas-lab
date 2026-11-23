#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Request YAML holding a secret value is refused with a 422 naming the field, never the value; YAML the secret
check cannot read (a tag, an alias, a broken or second document) is refused with its position only, before anything
is written or run."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from scope_fixtures import build_store, copy_scope_fixtures, make_client
from test_fastapi_app import client, demo_lab  # noqa: F401  (fixtures)

CANARY = "sk-S3CR3T-request-canary"
_LINES = {
    "literal_api_key": f"api_key: {CANARY}",
    "credentialed_url": f"url: https://u:{CANARY}@tools.example/mcp",
}


def _agent(line: str) -> str:
    return f"apiVersion: mas/v1\nkind: Agent\nmetadata:\n  name: t\nspec:\n  llm:\n    {line}\n"


def _overlay(line: str) -> str:
    return f"apiVersion: mas/v1\nkind: Overlay\nmetadata:\n  name: ov\nspec:\n  llm:\n    {line}\n"


def _pipeline(line: str) -> str:
    return f"pipeline:\n  name: p\n  steps:\n  - name: s\n    type: export_otel\n    config:\n      {line}\n"


def _experiment(line: str) -> str:
    return f"experiment:\n  name: e\n  vars:\n    {line}\n"


def _requests(line: str) -> list[tuple[str, str, dict]]:
    lib = "/api/libraries/demo"
    return [
        ("post", f"{lib}/validate", {"manifest_yaml": _agent(line)}),
        ("post", f"{lib}/run", {"manifest_yaml": _agent(line), "query": "q"}),
        ("post", f"{lib}/run/multi-turn", {"manifest_yaml": _agent(line), "queries": ["q"]}),
        ("post", f"{lib}/run-mas", {"manifest_yaml": _agent(line), "query": "q"}),
        ("post", f"{lib}/overlays/validate", {"manifest_yaml": _overlay(line)}),
        ("post", f"{lib}/overlays", {"name": "leak", "content": _overlay(line), "run_validation": False}),
        (
            "put",
            f"{lib}/overlays/cot-moderator",
            {"name": "cot-moderator", "content": _overlay(line), "run_validation": False},
        ),
        ("post", f"{lib}/pipelines/validate", {"manifest_yaml": _pipeline(line)}),
        ("post", f"{lib}/pipelines", {"name": "leak", "content": _pipeline(line), "run_validation": False}),
        ("post", f"{lib}/pipeline/run", {"pipeline_yaml": _pipeline(line)}),
        ("post", f"{lib}/experiments", {"name": "leak", "content": _experiment(line)}),
        ("post", f"{lib}/benchmark/run", {"experiment_yaml": _experiment(line)}),
        ("post", f"{lib}/apps", {"mas_name": "leak", "mas_yaml": "kind: MAS\n", "agents": {"a": _agent(line)}}),
        ("post", "/api/runs", {"scope": "demo", "kind": "agent", "item": "a", "manifest": _agent(line)}),
        ("post", f"{lib}/datasets", {"name": "leak.yaml", "content": _agent(line)}),
        ("put", f"{lib}/datasets/benchmark.yaml", {"name": "benchmark.yaml", "content": _agent(line)}),
    ]


@pytest.mark.parametrize("case", sorted(_LINES))
def test_request_yaml_with_a_secret_is_refused(client, demo_lab: Path, case: str) -> None:  # noqa: F811
    for method, url, body in _requests(_LINES[case]):
        response = getattr(client, method)(url, json=body)
        assert response.status_code == 422, (url, response.status_code, response.text[:200])
        assert CANARY not in response.text, url
        assert "llm" in response.text or "vars" in response.text or "config" in response.text, url
    assert not list(demo_lab.rglob("leak*"))
    assert CANARY not in (demo_lab / "overlays" / "cot-moderator.yaml").read_text(encoding="utf-8")
    assert CANARY not in (demo_lab / "datasets" / "benchmark.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("case", sorted(_LINES))
def test_tool_listing_skips_a_refused_file(client, demo_lab: Path, caplog, case: str) -> None:  # noqa: F811
    (demo_lab / "tools" / "leak.tool.yaml").write_text(
        f"kind: Tool\nmetadata:\n  name: leak\nspec:\n  params:\n    {_LINES[case]}\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/libraries/demo/tools")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["tools"]}
    assert "global/calc" in names
    assert "global/leak" not in names
    assert "leak.tool.yaml" in caplog.text
    assert CANARY not in caplog.text


_BROKEN = {
    "custom_tag": f"extra: !include {{api_key: {CANARY}}}",
    "core_tag_int": f"api_key: !!int {CANARY}",
    "alias": f"api_key: *{CANARY}",
    "same_line": f"api_key: {CANARY} : [",
    "tab": f"api_key: {CANARY}\n\tx: 1",
    "second_document": f"api_key: {CANARY}\n---\nfoo: 1",
}
_LIB = "/api/libraries/demo"


def _unreadable_requests(line: str) -> list[tuple[str, str, dict]]:
    doc = _agent(line)
    return [
        ("post", f"{_LIB}/validate", {"manifest_yaml": doc}),
        ("post", f"{_LIB}/pipelines/validate", {"manifest_yaml": doc}),
        ("post", "/api/scopes/library:demo/validate", {"manifest": doc}),
        ("post", f"{_LIB}/overlays", {"name": "leak", "content": doc, "run_validation": True}),
        ("post", f"{_LIB}/pipelines", {"name": "leak", "content": doc, "run_validation": False}),
        ("post", f"{_LIB}/pipeline/run", {"pipeline_yaml": doc}),
        ("post", f"{_LIB}/experiments", {"name": "leak", "content": doc}),
        ("post", f"{_LIB}/benchmark/run", {"experiment_yaml": doc}),
        ("post", f"{_LIB}/apps", {"mas_name": "leak", "mas_yaml": "kind: MAS\n", "agents": {"a": doc}}),
        ("put", f"{_LIB}/datasets/benchmark.yaml", {"name": "benchmark.yaml", "content": doc}),
        ("post", f"{_LIB}/run-mas", {"manifest_yaml": doc, "query": "q"}),
        ("post", "/api/runs", {"scope": "demo", "kind": "agent", "item": "a", "manifest": doc}),
    ]


@pytest.fixture
def jobs_db(tmp_path: Path):
    from mas.lab.controller.job_store import init_job_store

    path = tmp_path / "state" / "jobs.sqlite"
    init_job_store(db_path=path)
    yield path
    init_job_store(db_path=None)


def _canary_on_disk(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and CANARY.encode() in p.read_bytes()]


@pytest.mark.parametrize("case", sorted(_BROKEN))
def test_unreadable_yaml_is_refused_by_position(client, demo_lab: Path, jobs_db: Path, case: str) -> None:  # noqa: F811
    # each request meets two of the cases, each case four of the requests
    for method, url, body in _unreadable_requests(_BROKEN[case])[sorted(_BROKEN).index(case) % 3 :: 3]:
        response = getattr(client, method)(url, json=body)
        assert response.status_code == 422, (url, response.status_code, response.text[:200])
        assert CANARY not in response.text and "YAML does not parse" in response.text, url
    assert _canary_on_disk(demo_lab) == []
    with sqlite3.connect(jobs_db) as conn:
        assert conn.execute("SELECT error FROM jobs").fetchall() == []


def test_job_worker_and_chat_errors_name_a_yaml_error_by_position(tmp_path: Path, capsys) -> None:
    import yaml
    from mas.lab.controller.agent_chat import run_agent_turn
    from mas.lab.controller.jobs import job_error_text
    from mas.lab.controller.workers import _print_failure

    try:
        yaml.safe_load(f"api_key: {CANARY} : [\n")
    except yaml.YAMLError as exc:
        try:
            raise RuntimeError(f"benchmark failed: {exc}") from exc
        except RuntimeError as wrapped:
            text = job_error_text(wrapped)
        _print_failure()
    err = capsys.readouterr().err
    assert text.startswith("RuntimeError: ") and "line 1" in text and "line 1" in err
    result = run_agent_turn(f"api_key: {CANARY} : [\n", "hi", base_dir=tmp_path)
    assert result.status == "error" and "line 1" in result.error_message
    assert CANARY not in text + err + result.error_message + result.error_detail and "Traceback" not in err


@pytest.fixture
def scope_client(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    return make_client(build_store(roots), monkeypatch), roots


@pytest.mark.parametrize("line", [f"api_key: {CANARY}", _BROKEN["same_line"]])
def test_an_upload_or_item_update_with_a_secret_writes_nothing(scope_client, line: str) -> None:
    from mas.lab.controller.uploads import get_upload_store

    http, roots = scope_client
    before = set(get_upload_store().root.iterdir())
    response = http.post("/api/uploads", files={"file": ("leak.yaml", _agent(line).encode(), "application/x-yaml")})
    assert response.status_code == 422 and CANARY not in response.text
    assert set(get_upload_store().root.iterdir()) == before
    item = "/api/scopes/library:versioned_lib/items/application/plain"
    current = http.get(item).json()
    body = {"manifest": _agent(line), "children": current["children"], "revision": current["revision"]}
    response = http.put(item, json=body)
    assert response.status_code == 422 and CANARY not in response.text
    assert _canary_on_disk(roots["versioned_lib"]) == []
