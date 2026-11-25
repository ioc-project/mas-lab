#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Directory media store. The host passes the path; this module does not read the environment."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mas.runtime.contracts.content_types import ContentPart, MediaMissing, MediaStore

_IMAGE_PREFIX = "image/"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def media_directory(output_dir: Path) -> Path:
    """``media/`` next to the run's events."""
    return Path(output_dir) / "media"


def directory_store_for_events_file(events_file: str | Path | None) -> DirectoryMediaStore:
    """Store next to the events file, or under the XDG state root when that is unset."""
    if events_file:
        events_path = Path(events_file)
        # events.jsonl lives in traces/; media sits beside traces/.
        output_dir = events_path.parent.parent if events_path.parent.name == "traces" else events_path.parent
        return DirectoryMediaStore(media_directory(output_dir))
    from mas.runtime.xdg import mas_state_root

    return DirectoryMediaStore(mas_state_root() / "media")


class DirectoryMediaStore(MediaStore):
    """Files named by sha256 under a host-supplied directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, media_type: str, name: str) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / digest
        if not path.exists():
            path.write_bytes(data)
        kind = "image" if str(media_type).startswith(_IMAGE_PREFIX) else "file"
        return ContentPart.media_part(
            type=kind,
            media_type=media_type,
            sha256=digest,
            name=name,
            bytes=len(data),
        ).to_dict()

    def get(self, sha256: str) -> bytes:
        digest = str(sha256)
        if not _SHA256_HEX.fullmatch(digest):
            raise MediaMissing(digest)
        root = self.root.resolve()
        path = (root / digest).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise MediaMissing(digest) from None
        if not path.is_file():
            raise MediaMissing(digest)
        return path.read_bytes()
