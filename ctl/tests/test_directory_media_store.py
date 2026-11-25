#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Directory media store: files named by sha256, no paths in parts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from mas.ctl.media import DirectoryMediaStore
from mas.runtime.contracts.content_types import MediaMissing


def test_put_get_round_trip(tmp_path: Path):
    store = DirectoryMediaStore(tmp_path / "media")
    data = b"\x89PNG\r\n"
    part = store.put(data, "image/png", "chart.png")
    digest = hashlib.sha256(data).hexdigest()
    assert part["type"] == "image"
    assert part["sha256"] == digest
    assert part["name"] == "chart.png"
    assert part["bytes"] == len(data)
    assert "path" not in part
    assert store.get(digest) == data
    assert (tmp_path / "media" / digest).read_bytes() == data


def test_put_file_part_for_pdf(tmp_path: Path):
    store = DirectoryMediaStore(tmp_path / "media")
    part = store.put(b"%PDF-1.4", "application/pdf", "doc.pdf")
    assert part["type"] == "file"
    assert part["media_type"] == "application/pdf"


def test_get_missing_raises(tmp_path: Path):
    store = DirectoryMediaStore(tmp_path / "media")
    with pytest.raises(MediaMissing, match="missing"):
        store.get("0" * 64)


def test_get_rejects_non_hex_and_path_escape(tmp_path: Path):
    store = DirectoryMediaStore(tmp_path / "media")
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"outside")
    with pytest.raises(MediaMissing):
        store.get("../secret.bin")
    with pytest.raises(MediaMissing):
        store.get("/etc/passwd")
    with pytest.raises(MediaMissing):
        store.get("not-a-digest")
    assert secret.read_bytes() == b"outside"


def test_put_refuses_non_pdf_file(tmp_path: Path):
    store = DirectoryMediaStore(tmp_path / "media")
    from mas.runtime.contracts.content_types import ModalityNotSupported

    with pytest.raises(ModalityNotSupported, match="PDF"):
        store.put(b"hello", "text/plain", "notes.txt")
