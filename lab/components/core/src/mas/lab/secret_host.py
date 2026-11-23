#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Which secret store a lab process uses, as data a spawn worker or the controller daemon can rebuild.

One store per process: the CLI resolves it once (its flags, then the workspace ``secrets:`` block)
and installs it. Spawn workers rebuild it from :meth:`SecretHostSettings.to_payload`; the
controller daemon starts with the command's flags (:meth:`SecretHostSettings.args`) and refuses
a job that names another store (:func:`refuse_other_store`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from mas.runtime.secrets import SecretResolver


@dataclass(frozen=True)
class SecretHostSettings:
    store: str = "file"
    directory: str | None = None
    allow_loose_permissions: bool = False

    @classmethod
    def from_workspace(
        cls, workspace: Any, *, directory: str | None = None, store: str | None = None
    ) -> SecretHostSettings:
        from mas.ctl.secrets import secret_store_kind, secrets_block, secrets_dir_from_workspace

        kind = secret_store_kind(workspace, override=store)
        path = str(secrets_dir_from_workspace(workspace, override=directory)) if kind == "file" else None
        return cls(kind, path, secrets_block(workspace).get("allow_loose_permissions") is True)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SecretHostSettings:
        loose = bool(payload.get("allow_loose_permissions"))
        return cls(str(payload.get("store") or "file"), payload.get("directory"), loose)

    def args(self) -> list[str]:
        """``--secret-store`` / ``--secrets-dir`` for a child entry point (the controller daemon)."""
        return ["--secret-store", self.store] + (["--secrets-dir", self.directory] if self.directory else [])

    def install_process(self) -> SecretResolver:
        """Build the store and install it, with these settings, for the whole process."""
        from mas.ctl.secrets import build_resolver
        from mas.runtime.secrets import set_process_resolver

        resolver = build_resolver(
            directory=self.directory, store=self.store, allow_loose_permissions=self.allow_loose_permissions
        )
        set_process_resolver(resolver)
        set_process_settings(self)
        return resolver


_PROCESS: list[SecretHostSettings] = []


def set_process_settings(settings: SecretHostSettings | None) -> None:
    _PROCESS[:] = [settings] if settings is not None else []


def process_settings() -> SecretHostSettings | None:
    """Settings of the process store, ``None`` when no lab command installed one."""
    return _PROCESS[0] if _PROCESS else None


def refuse_other_store(payload: dict[str, Any] | None) -> None:
    """Daemon side: a job that names a store other than this process's is refused."""
    own, job = process_settings(), SecretHostSettings.from_payload(payload) if payload else None
    if own is not None and job is not None and (job.store, job.directory) != (own.store, own.directory):
        raise ValueError(
            f"the job uses the {job.store} secret store {job.directory or ''}, the controller "
            f"{own.store} {own.directory or ''}: restart the controller with that store (mas-lab control stop)"
        )
