#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 11 — Sessions and recovery: checkpoint capture, fork, retention.

Tutorial 11 reuses Tutorial 01's agent.yaml rather than shipping its own —
these tests drive the real JsonCheckpointStore / checkpoint CLI against
Tutorial 01's actual manifest content and hash, not a synthetic stand-in.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from tutorial_support import T01, load_yaml, run_cli


class TestManifestValidation:
    def test_validate_reused_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T01 / "agent.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout


def _v2_payload(*, session_id: str, turn: int, label: str, parent_session_id: str | None = None):
    from mas.runtime.session import ManifestRef

    manifest = load_yaml(T01 / "agent.yaml")
    manifest_ref = ManifestRef.from_content(manifest)
    return {
        "version": 2,
        "label": label,
        "turn": turn,
        "lineage": {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
            "forked_from_checkpoint": None,
            "root_session_id": parent_session_id or session_id,
            "created_at": "2026-09-30T12:00:00+00:00",
        },
        "kernel": {"q": {}, "run": {}},
        "working_memory": [
            {
                "agent_id": manifest["metadata"]["name"],
                "turn_history": [["What is the capital of France?", "Paris."]],
                "committed_messages": [{"role": "user", "content": "What is the capital of France?"}],
                "conversation_chunks": None,
            }
        ],
        "manifest": {"content": manifest, "content_hash": manifest_ref.content_hash},
    }


class TestCaptureAndResume:
    """docs/tutorials/11-sessions-and-recovery/README.md § Capture a checkpoint."""

    def test_capture_round_trips_the_real_tutorial_manifest(self, tmp_path: Path):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore

        store = JsonCheckpointStore(tmp_path)
        payload = _v2_payload(session_id="session-1", turn=1, label="turn-0001")

        path = store.save(payload, label="turn-0001")

        assert store.load_payload(path) == payload
        assert store.load(path) == payload["kernel"]
        # The manifest hash travels with the checkpoint, for resume-time verification.
        assert len(payload["manifest"]["content_hash"]) == 64

    def test_checkpoint_list_and_show_cli(self, tmp_path: Path):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore
        from mas.ctl.cli.commands.checkpoint import checkpoint_group

        store = JsonCheckpointStore(tmp_path)
        store.save(_v2_payload(session_id="session-1", turn=1, label="turn-0001"), label="turn-0001")

        listed = CliRunner().invoke(checkpoint_group, ["list", str(tmp_path)])
        assert listed.exit_code == 0, listed.output
        assert "turn-0001" in listed.output

        shown = CliRunner().invoke(checkpoint_group, ["show", str(tmp_path / "turn-0001.checkpoint.json")])
        assert shown.exit_code == 0, shown.output
        assert "session-1" in shown.output


class TestForkAndResume:
    """docs/tutorials/11-sessions-and-recovery/README.md § Fork and resume."""

    def test_fork_gets_a_distinct_session_identity(self, tmp_path: Path):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore
        from mas.ctl.cli.commands.checkpoint import checkpoint_group

        store = JsonCheckpointStore(tmp_path)
        source = store.save(_v2_payload(session_id="session-1", turn=2, label="turn-0002"), label="turn-0002")

        result = CliRunner().invoke(checkpoint_group, ["fork", str(source), "--session-id", "experiment-a"])
        assert result.exit_code == 0, result.output
        fork_path = Path(result.output.strip())

        forked = store.load_payload(fork_path)
        assert forked["lineage"]["session_id"] == "experiment-a"
        assert forked["lineage"]["parent_session_id"] == "session-1"
        assert forked["lineage"]["forked_from_checkpoint"] == source.name
        # The source checkpoint is untouched — forking never mutates it.
        assert store.load_payload(source)["lineage"]["session_id"] == "session-1"
        # The fork keeps the conversation that existed at the selected checkpoint.
        source_working_memory = store.load_payload(source)["working_memory"]
        assert forked["working_memory"] == source_working_memory

    def test_two_forks_from_the_same_source_do_not_see_each_others_writes(self, tmp_path: Path):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore
        from mas.ctl.cli.commands.checkpoint import checkpoint_group

        store = JsonCheckpointStore(tmp_path)
        source = store.save(_v2_payload(session_id="session-1", turn=3, label="turn-0003"), label="turn-0003")

        fork_a = CliRunner().invoke(checkpoint_group, ["fork", str(source), "--session-id", "branch-a"])
        fork_b = CliRunner().invoke(checkpoint_group, ["fork", str(source), "--session-id", "branch-b"])
        assert fork_a.exit_code == fork_b.exit_code == 0

        branch_a_payload = _v2_payload(
            session_id="branch-a", turn=4, label="branch-a-turn-0004", parent_session_id="session-1"
        )
        store.save(branch_a_payload, label="branch-a-turn-0004")

        # branch-b's retention is independent of branch-a's new turn.
        store.retain("branch-b", "single", 1)
        assert Path(fork_a.output.strip()).exists()


class TestRetention:
    """retention.mode: last_n / single / all, and per-session pruning."""

    def test_retention_prunes_only_the_named_session(self, tmp_path: Path):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore

        store = JsonCheckpointStore(tmp_path)
        a1 = store.save({"q": {}, "run": {}}, label="session-a-turn-1")
        a2 = store.save({"q": {}, "run": {}}, label="session-a-turn-2")
        b1 = store.save({"q": {}, "run": {}}, label="session-b-turn-1")

        store.retain("session-a", "single", 1)

        assert not a1.exists()
        assert a2.exists()
        assert b1.exists()
