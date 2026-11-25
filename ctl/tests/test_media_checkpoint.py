#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A media turn round-trips through a checkpoint when the media directory is present."""

from __future__ import annotations

from pathlib import Path

from mas.ctl.adapters.checkpoint import JsonCheckpointStore
from mas.ctl.media import DirectoryMediaStore, media_directory
from mas.runtime.session import ManifestRef

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_checkpoint_round_trips_committed_parts_with_media_dir(tmp_path: Path) -> None:
    output = tmp_path / "run"
    store_dir = DirectoryMediaStore(media_directory(output))
    part = store_dir.put(_PNG, "image/png", "chart.png")
    content = [{"type": "text", "text": "see"}, part]
    manifest = {"name": "agent-1"}
    payload = {
        "version": 2,
        "label": "media-turn",
        "turn": 1,
        "lineage": {
            "session_id": "session-1",
            "parent_session_id": None,
            "forked_from_checkpoint": None,
            "root_session_id": "session-1",
            "created_at": "2026-11-24T12:00:00+00:00",
        },
        "kernel": {"q": {}, "run": {}},
        "working_memory": [
            {
                "agent_id": "agent-1",
                "turn_history": [["see", "ok"]],
                "committed_messages": [{"role": "user", "content": content}],
                "conversation_chunks": None,
            }
        ],
        "manifest": {
            "content": manifest,
            "content_hash": ManifestRef.from_content(manifest).content_hash,
        },
    }
    checkpoints = JsonCheckpointStore(tmp_path / "checkpoints")
    path = checkpoints.save(payload, label="media-turn")
    restored = checkpoints.load_payload(path)
    restored_content = restored["working_memory"][0]["committed_messages"][0]["content"]
    assert restored_content == content
    assert restored_content[1]["sha256"] == part["sha256"]
    assert store_dir.get(part["sha256"]) == _PNG
    dumped = str(restored)
    assert "data:" not in dumped
    assert _PNG not in dumped.encode("utf-8", errors="ignore")
