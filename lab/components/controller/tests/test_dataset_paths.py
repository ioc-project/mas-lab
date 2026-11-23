#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Dataset routes never read, write or delete a file outside the library's datasets directory."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_fastapi_app import client, demo_lab  # noqa: F401  (fixtures)

LIB = "/api/libraries/demo/datasets"


@pytest.mark.parametrize("name", ["..%2F..%2Fvictim.yaml", "%2e%2e%2F%2e%2e%2Fvictim.yaml", "%2Fetc%2Fvictim.yaml"])
def test_a_path_outside_datasets_is_refused(client, demo_lab: Path, name: str) -> None:  # noqa: F811
    victim = demo_lab.parent / "victim.yaml"
    victim.write_text("keep: me\n", encoding="utf-8")
    assert client.get(f"{LIB}/{name}").status_code == 404
    assert client.delete(f"{LIB}/{name}").status_code == 404
    assert client.post(LIB, json={"name": name.replace("%2F", "/"), "content": "a: 1\n"}).status_code == 400
    assert victim.read_text(encoding="utf-8") == "keep: me\n"


def test_a_nested_dataset_reads_and_renames(client, demo_lab: Path) -> None:  # noqa: F811
    assert client.get(f"{LIB}/trip-planner/queries.yaml").status_code == 200
    body = {"name": "trip-planner/renamed.yaml", "content": "a: 1\n"}
    assert client.put(f"{LIB}/trip-planner/queries.yaml", json=body).status_code == 200
    assert (demo_lab / "datasets" / "trip-planner" / "renamed.yaml").read_text(encoding="utf-8") == "a: 1\n"
