#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import pytest
from mas.ctl.adapters.checkpoint import JsonCheckpointStore
from mas.ctl.cli.commands.chat import _embedded_checkpoint_manifest
from mas.runtime.session import ManifestRef


def test_checkpoint_can_supply_manifest_for_resume_without_source_file(tmp_path) -> None:
    manifest = {"kind": "Agent", "metadata": {"name": "portable-agent"}, "spec": {}}
    store = JsonCheckpointStore(tmp_path)
    path = store.save(
        {
            "version": 2,
            "label": "portable",
            "turn": 0,
            "lineage": {
                "session_id": "session",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "session",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {}, "run": {}},
            "working_memory": [],
            "manifest": {
                "content": manifest,
                "content_hash": ManifestRef.from_content(manifest).content_hash,
            },
        },
        label="portable",
    )

    assert _embedded_checkpoint_manifest(path) == manifest


def test_checkpoint_resume_rejects_tampered_embedded_manifest(tmp_path) -> None:
    store = JsonCheckpointStore(tmp_path)
    path = store.save(
        {
            "version": 2,
            "label": "tampered",
            "turn": 0,
            "lineage": {
                "session_id": "session",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "session",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": {"q": {}, "run": {}},
            "working_memory": [],
            "manifest": {"content": {"name": "agent"}, "content_hash": "0" * 64},
        },
        label="tampered",
    )
    with pytest.raises(Exception, match="hash mismatch"):
        _embedded_checkpoint_manifest(path)


def test_resume_without_manifest_or_workspace_root_anchors_at_checkpoint_directory(tmp_path) -> None:
    from types import SimpleNamespace

    from mas.ctl.infra.resolve import resolution_anchor
    from mas.ctl.session.bootstrap import _infra_anchor

    ckpt = tmp_path / "ckpt" / "c.json"
    ckpt.parent.mkdir()
    options = SimpleNamespace(manifest_dir=None, checkpoint_path=ckpt)
    ws = SimpleNamespace(root=None)
    anchor = _infra_anchor(options, ws)
    assert resolution_anchor(anchor, ws) == ckpt.parent.resolve()
    # A workspace root or a manifest directory still wins.
    assert _infra_anchor(options, SimpleNamespace(root=tmp_path)) is None
    assert _infra_anchor(SimpleNamespace(manifest_dir=tmp_path, checkpoint_path=ckpt), ws) == tmp_path
