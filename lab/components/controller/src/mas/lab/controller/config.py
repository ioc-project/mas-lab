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


_RUN_ENV_PREFIXES = ("MAS_", "OPENAI_", "AZURE_", "ANTHROPIC_", "LLM_", "LITELLM_", "OTEL_")
_RUN_ENV_NAMES = frozenset(
    {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"}
)
# Set by the client/daemon machinery itself, not by the user.
_DAEMON_ENV_PREFIXES = ("MAS_CONTROLLER_", "MAS_DOTENV_LOADED")


def env_fingerprint(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Hash of each environment variable that can change a run; values never leave the process."""
    import hashlib

    env = hostenv.snapshot() if environ is None else environ
    return {
        name: hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        for name, value in env.items()
        if (name.startswith(_RUN_ENV_PREFIXES) or name in _RUN_ENV_NAMES) and not name.startswith(_DAEMON_ENV_PREFIXES)
    }
