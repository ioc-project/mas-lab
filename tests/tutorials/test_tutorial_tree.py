#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorials ship only the files a reader needs.

Cognitive-failure catalogs, per-agent run traces, and C-class labels stay
local (worktree-root ``failure.bak``, gitignored). Restore by renaming to
``.md`` into the tutorial that needs it; this test fails until then.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# C1–C9 challenge ids and "cognitive failure" write-ups are not OSS yet.
_C_CLASS = re.compile(r"\((C[1-9]|C1[0-9])\)|\bC[1-9]_VIOLATION\b|\bC8\b")
_COGNITIVE = re.compile(r"cognitive fail", re.IGNORECASE)
_TEXT_SUFFIX = {".md", ".py", ".yaml", ".yml", ".txt"}


def _tracked(*paths: str) -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z", *paths], cwd=REPO)
    return [p.decode() for p in out.split(b"\0") if p]


def test_tutorials_do_not_ship_failure_catalogs_or_agent_traces() -> None:
    leaked = []
    for rel in _tracked("docs/tutorials"):
        parts = Path(rel).parts
        if "failures" in parts:
            leaked.append(rel)
        if "traces" in parts and "agents" in parts:
            leaked.append(rel)
        name = Path(rel).name.lower()
        if name.startswith("c8-") or "cognitive-fail" in name:
            leaked.append(rel)
    assert not leaked, (
        "Unexpected tutorial files (keep catalogs in local failure.bak): "
        + ", ".join(leaked)
    )


def test_tutorials_do_not_name_cognitive_failures() -> None:
    hits = []
    for rel in _tracked("docs/tutorials"):
        if Path(rel).suffix.lower() not in _TEXT_SUFFIX:
            continue
        text = (REPO / rel).read_text(encoding="utf-8")
        if _C_CLASS.search(text) or _COGNITIVE.search(text):
            hits.append(rel)
    assert not hits, "Cognitive-failure labels in tutorials (use local failure.bak): " + ", ".join(
        hits
    )


def test_sample_library_does_not_ship_failure_catalogs() -> None:
    leaked = [
        rel
        for rel in _tracked("library-samples")
        if "failures" in Path(rel).parts or Path(rel).name.lower().startswith("c8-")
    ]
    assert not leaked, (
        "Failure catalogs belong in local failure.bak, not library-samples: "
        + ", ".join(leaked)
    )
