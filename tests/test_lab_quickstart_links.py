#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    REPO_ROOT / "lab" / "README.md",
    REPO_ROOT / "library-samples" / "tools" / "README.md",
]


def test_common_lab_quickstart_is_linked_from_main_lab_entrypoints() -> None:
    for file_path in TARGETS:
        text = file_path.read_text(encoding="utf-8")
        assert "labs-quickstart" in text, f"{file_path}: missing labs-quickstart link"
        assert "labs-going-further" in text, f"{file_path}: missing labs-going-further link"
