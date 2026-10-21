#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Explicit controller settings (no environment variables in runtime code)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


_LOOPBACK_UI_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:8090",
    "http://127.0.0.1:8090",
)


def is_loopback_host(host: str) -> bool:
    """True for binds that only listen on the local machine."""
    return host in {"127.0.0.1", "localhost", "::1"}


@dataclass
class ControllerSettings:
    """Flags the daemon / ``mas-lab serve`` pass into :func:`create_app`."""

    host: str = "127.0.0.1"
    port: int = 8090
    cors_origins: list[str] = field(default_factory=list)
    token: str | None = None
    state_dir: Path | None = None
    api_version: str = "0.3.0"

    @property
    def auth_mode(self) -> str:
        return "bearer" if self.token else "none"

    @property
    def cors_allow_origins(self) -> list[str]:
        if self.cors_origins:
            return list(self.cors_origins)
        if is_loopback_host(self.host):
            return list(_LOOPBACK_UI_ORIGINS)
        return []

    @property
    def resolved_state_dir(self) -> Path:
        if self.state_dir is not None:
            return Path(self.state_dir)
        from mas.lab.controller.config import controller_dir

        return controller_dir() / "state"


def load_token_file(path: Path) -> str:
    """Read a bearer token from a file. Empty files are treated as no token."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"token file {path} is empty")
    return text
