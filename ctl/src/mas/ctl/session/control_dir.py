#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Default directory for control-protocol advertisements.

User-owned sockets belong in a runtime directory, not a durable data path.
Prefer ``$XDG_RUNTIME_DIR`` (Linux ``/run/user/<uid>``), then ``/var/run``,
then the process temp dir (typical on macOS, where ``/var/run`` is not
writable). Override with ``MAS_CONTROL_DIR``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from mas.runtime import hostenv


def default_control_dir() -> Path:
    """Return a writable per-user control advertisement directory."""
    override = str(hostenv.get("MAS_CONTROL_DIR") or "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    xdg = str(hostenv.get("XDG_RUNTIME_DIR") or "").strip()
    if xdg:
        candidates.append(Path(xdg) / "mas-ctl")
    try:
        uid = os.getuid()
    except AttributeError:
        uid = None
    if uid is not None:
        candidates.append(Path("/run/user") / str(uid) / "mas-ctl")
    candidates.append(Path("/var/run/mas-ctl"))
    candidates.append(Path(tempfile.gettempdir()) / "mas-ctl")
    last_error: OSError | None = None
    seen: set[Path] = set()
    for path in candidates:
        resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            resolved.mkdir(parents=True, exist_ok=True)
            probe = resolved / ".writable"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return resolved
        except OSError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"no writable control directory (tried {candidates!r})") from last_error
