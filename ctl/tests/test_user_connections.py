#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The user connections file gets the manifest secret checks when it loads."""

from __future__ import annotations

from pathlib import Path

import pytest
from mas.ctl.workspace.user_connections import ConnectionsFileError, load_user_connections

CANARY = "S3CR3T-connections-canary"


def _write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "mas" / "connections.yaml"
    path.parent.mkdir()
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "entry",
    [
        f"neo4j:\n    uri: bolt://u:{CANARY}@graph:7687\n",
        f"clickhouse:\n    host: u:{CANARY}@evil\n",
        f"clickhouse:\n    host: localhost\n    password: {CANARY}\n",
    ],
)
def test_a_connections_file_with_a_credential_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    path = _write(tmp_path, monkeypatch, "connections:\n  " + entry)
    with pytest.raises(ConnectionsFileError) as caught:
        load_user_connections()
    assert str(path) in caught.value.message and "connections." in caught.value.message
    assert CANARY not in caught.value.message


def test_a_connections_file_with_refs_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        monkeypatch,
        "connections:\n  neo4j:\n    uri: bolt://graph:7687\n    password: {secret: neo4j}\n",
    )
    assert load_user_connections()["connections"]["neo4j"]["password"] == {"secret": "neo4j"}
