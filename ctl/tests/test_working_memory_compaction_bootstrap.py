#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""instantiate_runtime() wires spec.working_memory.compaction into context_manager."""

from pathlib import Path

from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime


def _instantiate(manifest: dict, tmp_path: Path, monkeypatch, **options):
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.workspace.config import UserConfig, WorkspaceConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_API_KEY", "ci-test-unused")
    spec = manifest.setdefault("spec", {})
    spec.setdefault("models", [{"id": "main", "model": "gpt-4o"}])
    infra = resolve_infra_refs(["standard:openai"], anchor=tmp_path)
    return instantiate_runtime(
        InstantiationOptions(
            agent_manifest=manifest,
            manifest_dir=tmp_path,
            resolved_infra=infra,
            enable_observability=False,
            enable_governance=False,
            **options,
        )
    )


def test_keep_recent_compaction_is_wired_into_the_manifests_context_manager(tmp_path: Path, monkeypatch):
    manifest = {
        "metadata": {"name": "agent"},
        "spec": {
            "working_memory": {"compaction": {"strategy": "keep_recent", "max_messages": 3}},
        },
    }
    _instantiate(manifest, tmp_path, monkeypatch)
    assert manifest["spec"]["context_manager"] == {"type": "stack", "params": {"max_messages": 3}}


def test_explicit_context_manager_is_left_untouched(tmp_path: Path, monkeypatch):
    manifest = {
        "metadata": {"name": "agent"},
        "spec": {
            "context_manager": {"type": "sliding_window", "params": {"window_size": 9}},
            "working_memory": {"compaction": {"strategy": "keep_recent", "max_messages": 3}},
        },
    }
    _instantiate(manifest, tmp_path, monkeypatch)
    assert manifest["spec"]["context_manager"] == {"type": "sliding_window", "params": {"window_size": 9}}


def test_no_working_memory_compaction_leaves_spec_without_context_manager(tmp_path: Path, monkeypatch):
    """Omitted context_manager stays omitted; the default summarising plugin
    is instantiated at assemble time and binds the engine from ctx."""
    manifest = {
        "metadata": {"name": "agent"},
        "spec": {},
    }
    instance, _store = _instantiate(manifest, tmp_path, monkeypatch)
    assert "context_manager" not in manifest["spec"]
    assert getattr(instance.driver.ctx, "engine", None) is instance.driver.engine


def test_summarize_sugar_writes_context_manager_without_runtime_callables(tmp_path: Path, monkeypatch):
    """standard:openai resolves to a LiveLlmEngine; the engine is bound on
    ctx, not stuffed into spec.context_manager.params."""
    manifest = {
        "metadata": {"name": "agent"},
        "spec": {
            "working_memory": {"compaction": {"strategy": "summarize", "keep_turns": 4}},
        },
    }
    instance, _store = _instantiate(manifest, tmp_path, monkeypatch)
    cm = manifest["spec"]["context_manager"]
    assert cm["type"] == "summarising"
    assert cm["params"]["keep_turns"] == 4
    assert "summarize_fn" not in cm["params"]
    assert getattr(instance.driver.ctx, "engine", None) is instance.driver.engine


def test_in_memory_resume_does_not_keep_a_disk_store(tmp_path: Path, monkeypatch):
    from mas.ctl.adapters.checkpoint import InMemoryCheckpointStore, JsonCheckpointStore
    from mas.runtime.session import ManifestRef

    manifest = {
        "metadata": {"name": "agent"},
        "spec": {"checkpoint": {"mode": "in_memory"}},
    }
    instance, _ = _instantiate(manifest, tmp_path, monkeypatch)
    kernel = instance.snapshot()
    disk = JsonCheckpointStore(tmp_path)
    path = disk.save(
        {
            "version": 2,
            "label": "seed",
            "turn": 1,
            "lineage": {
                "session_id": "s",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "s",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": kernel,
            "working_memory": [],
            "manifest": {
                "content": manifest,
                "content_hash": ManifestRef.from_content(manifest).content_hash,
            },
        },
        label="seed",
    )
    _, store = _instantiate(manifest, tmp_path, monkeypatch, checkpoint_path=path)
    assert isinstance(store, InMemoryCheckpointStore)


def test_hybrid_storage_builds_a_hybrid_store(tmp_path: Path, monkeypatch):
    from mas.library.standard.plugins.checkpoint import HybridCheckpointStore

    manifest = {
        "metadata": {"name": "agent"},
        "spec": {"checkpoint": {"mode": "every_turn", "storage": "hybrid"}},
    }
    _, store = _instantiate(manifest, tmp_path, monkeypatch, checkpoint_dir=tmp_path)
    assert isinstance(store, HybridCheckpointStore)


def test_hybrid_checkpoint_path_keeps_the_hybrid_store(tmp_path: Path, monkeypatch):
    from mas.ctl.adapters.checkpoint import JsonCheckpointStore
    from mas.runtime.session import ManifestRef

    manifest = {
        "metadata": {"name": "agent"},
        "spec": {"checkpoint": {"mode": "every_turn", "storage": "hybrid"}},
    }
    instance, _ = _instantiate(manifest, tmp_path, monkeypatch, checkpoint_dir=tmp_path)
    kernel = instance.snapshot()
    path = JsonCheckpointStore(tmp_path).save(
        {
            "version": 2,
            "label": "seed",
            "turn": 1,
            "lineage": {
                "session_id": "s",
                "parent_session_id": None,
                "forked_from_checkpoint": None,
                "root_session_id": "s",
                "created_at": "2026-09-30T12:00:00+00:00",
            },
            "kernel": kernel,
            "working_memory": [],
            "manifest": {
                "content": manifest,
                "content_hash": ManifestRef.from_content(manifest).content_hash,
            },
        },
        label="seed",
    )
    _, store = _instantiate(
        manifest,
        tmp_path,
        monkeypatch,
        checkpoint_dir=tmp_path,
        checkpoint_path=path,
    )
    from mas.library.standard.plugins.checkpoint import HybridCheckpointStore

    assert isinstance(store, HybridCheckpointStore)
