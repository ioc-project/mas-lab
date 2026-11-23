#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Writes to the file store: create it, set one secret, import a dotenv file. Never logs a value."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from mas.runtime.secrets import SecretDenied, SecretRef
from mas.runtime.secrets.env_names import KNOWN_ENV_NAMES
from mas.runtime.secrets.mode import check_private_mode
from mas.runtime.secrets.value import clean_secret_bytes


def _check_private_dir(path: Path, kind: str) -> None:
    st = path.lstat()
    problem = check_private_mode(st, kind=kind) if stat.S_ISDIR(st.st_mode) else f"{kind} is not a directory"
    if problem:
        raise PermissionError(f"{problem}; run chmod 700 on it")


def init_secrets_dir(directory: Path) -> Path:
    """Create *directory* (resolved) with mode 0700 and a ``.gitignore`` of ``*``.

    An existing directory is never changed: it is refused when it is not a private directory of this user.
    """
    directory = Path(directory).expanduser().resolve()
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir(mode=0o700)
    except FileExistsError:
        _check_private_dir(directory, f"secrets directory {directory}")
        return directory
    os.chmod(directory, 0o700)
    fd = os.open(directory / ".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("*\n")
    return directory


def write_file_secret(
    directory: Path,
    name: str,
    value: str | bytes,
    *,
    key: str = "value",
    max_bytes: int | None = None,
) -> Path:
    """Write one secret file (mode 0400) under *directory*, atomically. Never logs the value.

    Key ``value`` writes ``<name>``, or ``<name>/value`` when ``<name>/`` is a keyed directory; another
    key writes ``<name>/<key>``. The value goes to a dotted temporary file in the same directory, is
    synced, made 0400, then renamed over the target. A value over *max_bytes* (the file store's cap by
    default) is refused, since the store would refuse to read it.
    """
    from mas.library.standard.plugins.secrets.file_store import DEFAULT_MAX_BYTES

    ref = SecretRef(name=name, key=key)
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if len(raw) > (DEFAULT_MAX_BYTES if max_bytes is None else max_bytes):
        raise SecretDenied(ref, "too large")
    payload = clean_secret_bytes(ref, raw)
    del raw
    if not payload:
        raise SecretDenied(ref, "is empty")
    parent = init_secrets_dir(directory)
    path = parent / name
    if key != "value" or (path.is_dir() and not path.is_symlink()):
        if not os.path.lexists(path):
            path.mkdir(mode=0o700)
        _check_private_dir(path, "keyed directory")
        parent, path = path, path / key
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IRUSR)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def import_env_file(path: Path, directory: Path) -> tuple[list[str], list[str]]:
    """Write one secret per known variable of a dotenv file; return the refs written and the names skipped.

    Known: the provider variables of :data:`KNOWN_ENV_NAMES` and the ``MAS_SECRET_*`` names of the env
    store. Every other variable is skipped. Does not export to os.environ.
    """
    from dotenv import dotenv_values
    from mas.library.standard.plugins.secrets.env_store import ref_for_env_var

    imported: list[str] = []
    skipped: list[str] = []
    for env_name, value in dotenv_values(path).items():
        if not env_name or not value:
            continue
        known = KNOWN_ENV_NAMES.get(env_name)
        ref = SecretRef(name=known) if known else ref_for_env_var(env_name)
        if ref is None:
            skipped.append(env_name)
            continue
        write_file_secret(directory, ref.name, value, key=ref.key)
        imported.append(str(ref))
    return imported, skipped
