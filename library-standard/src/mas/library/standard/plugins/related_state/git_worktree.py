#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Git adapter for related filesystem state.

Uses the system ``git`` binary (libgit2/pygit2 optional later). The
snapshot holds a commit hash, not the tree bytes. Commits land on
``refs/mas-lab/...`` with a private index file so HEAD and the user's
index do not move.

Specialized filesystems (overlayfs, virtiofs, git-worktree) implement
the same ``RelatedStatePlugin`` and stay out of Q.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from mas.runtime.boundary.related_state import RelatedStateRef
from mas.runtime.secrets import child_env


class GitWorktreeRelatedState:
    """Fingerprint = commit of the workdir, stored off the user's HEAD."""

    name = "git_worktree"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def capture(self, session_id: str) -> RelatedStateRef:
        """Commit the execute root to ``refs/mas-lab/...`` without moving HEAD."""
        if not shutil.which("git") or not (self.root / ".git").exists():
            digest = _tree_hash(self.root)
            return RelatedStateRef(
                plugin=self.name,
                fingerprint=digest,
                locator=str(self.root),
                adapter="content-hash",
            )
        index = {"GIT_INDEX_FILE": str(self.root / ".git" / "mas-lab-index")}
        _git(self.root, "add", "-A", extra=index)
        tree = _git(self.root, "write-tree", extra=index).strip()
        commit = _git(
            self.root,
            "-c",
            "user.email=mas-lab@localhost",
            "-c",
            "user.name=mas-lab",
            "commit-tree",
            tree,
            "-m",
            f"mas-related-state {session_id}",
            extra=index,
        ).strip()
        _git(self.root, "update-ref", f"refs/mas-lab/{session_id}/{commit[:12]}", commit)
        return RelatedStateRef(
            plugin=self.name,
            fingerprint=commit,
            locator=str(self.root),
            adapter="git",
        )

    def restore(self, session_id: str, ref: RelatedStateRef) -> None:
        if ref.adapter == "git" and ref.fingerprint:
            _git(self.root, "restore", "--source", ref.fingerprint, "--worktree", ".")
            _git(self.root, "clean", "-fd")


def _git(root: Path, *args: str, extra: dict[str, str] | None = None) -> str:
    """Run git with the child allowlist plus *extra*, never the process's environment."""
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=child_env(extra),
    )
    return proc.stdout


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            h.update(str(path.relative_to(root)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()
