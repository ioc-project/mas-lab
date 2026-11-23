#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Controller daemon paths and defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from mas.runtime import hostenv
from mas.runtime.xdg import mas_data_root

DEFAULT_HTTP_PORT = int(hostenv.get("MAS_CONTROLLER_PORT", "9000"))


def mas_dir() -> Path:
    return Path(hostenv.get("MAS_HOME", mas_data_root()))


def controller_dir() -> Path:
    return Path(hostenv.get("MAS_CONTROLLER_DIR", mas_dir() / "controller"))


def socket_path() -> Path:
    return Path(hostenv.get("MAS_CONTROLLER_SOCKET", mas_dir() / "controller.sock"))


def pid_path() -> Path:
    return Path(hostenv.get("MAS_CONTROLLER_PID", controller_dir() / "controller.pid"))


def ensure_mas_dirs() -> None:
    mas_dir().mkdir(parents=True, exist_ok=True)
    controller_dir().mkdir(parents=True, exist_ok=True)


def code_fingerprint() -> str:
    """Identify the interpreter and installed ``mas.*`` sources.

    The daemon keeps the modules it imported at startup, so a client compares
    this value with the daemon's to detect an upgrade (wheel or editable).
    """
    import hashlib
    import sys

    import mas

    digest = hashlib.sha256(sys.executable.encode("utf-8"))
    for root in sorted(mas.__path__):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                st = os.stat(path)
                digest.update(f"{path}:{st.st_size}:{st.st_mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


def env_fingerprint(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The declared non-secret scheduler keys a run depends on (``MAS_LLM_*``), as plain values.

    The daemon and its client compare them. A secret, or anything from the secret store, is
    never part of it: credentials are read from the store at the moment of use by both.
    """
    from mas.ctl.session.llm_scheduler_env import scheduler_fingerprint

    return scheduler_fingerprint(environ)
