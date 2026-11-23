#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""File secret store: one file per secret under a directory.

A read of ``<dir>/<name>[/<key>]`` refuses a symlink (``lstat`` on the name, ``O_NOFOLLOW`` on the
file), then checks the descriptor it opened (``fstat``): regular file, owner, mode. The bytes come
from that descriptor, read up to the size cap. The store directory and a keyed directory are owner
checked and private, so only the owner can swap an entry. ``describe`` and ``change_token`` run the
same code.

On Windows there are no owner or mode bits, so the store refuses to read unless
``allow_loose_permissions`` is set. That flag also turns off the owner and mode checks.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path
from typing import Hashable

from mas.runtime.secrets.errors import SecretDenied, SecretMissing
from mas.runtime.secrets.mode import check_private_mode
from mas.runtime.secrets.ref import SecretRef
from mas.runtime.secrets.store import SecretInfo
from mas.runtime.secrets.value import SecretValue, clean_secret_bytes

DEFAULT_MAX_BYTES = 1_048_576
_NO_STORE = "the secret store directory does not exist; run `mas-ctl secrets init`"
_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)


class FileSecretStore:
    """``<dir>/<name>`` or ``<dir>/<name>/<key>``. Ctl passes the directory."""

    def __init__(
        self,
        directory: str | Path,
        *,
        allow_loose_permissions: bool = False,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.allow_loose_permissions = bool(allow_loose_permissions)
        self.max_bytes = int(max_bytes)

    def get(self, ref: SecretRef) -> SecretValue:
        return SecretValue(self._open(ref, read=True)[0], name=ref.name)

    def describe(self, ref: SecretRef) -> SecretInfo:
        """The checks of ``get``, the read and the value checks included. Size is the value's byte size."""
        try:
            size = len(self._open(ref, read=True)[0])
        except (SecretMissing, SecretDenied) as exc:
            return SecretInfo(present=False, size=0, reason=exc.reason)
        return SecretInfo(present=True, size=size)

    def change_token(self, ref: SecretRef) -> Hashable:
        try:
            _, st = self._open(ref, read=False)
        except (SecretMissing, SecretDenied):
            return None
        return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)

    def _open(self, ref: SecretRef, *, read: bool) -> tuple[bytes, os.stat_result]:
        if sys.platform == "win32" and not self.allow_loose_permissions:
            raise SecretDenied(ref, "has no permission checks on Windows; set allow_loose_permissions")
        try:
            self._check(ref, os.stat(self.directory), "directory")
        except FileNotFoundError:
            # No store at all: every secret is absent, so an optional ref resolves to nothing.
            raise SecretMissing(ref, reason="directory missing", hint=_NO_STORE) from None
        except OSError:
            raise SecretDenied(ref, "unreadable") from None
        path = self.directory / ref.name
        try:
            top = os.lstat(path)
        except FileNotFoundError:
            raise SecretMissing(ref) from None
        except OSError:
            raise SecretDenied(ref, "unreadable") from None
        if stat.S_ISLNK(top.st_mode):
            raise SecretDenied(ref, "is a symlink")
        if stat.S_ISDIR(top.st_mode):
            # ``{secret: NAME}`` on a keyed directory reads ``NAME/value``.
            self._check(ref, top, "keyed directory")
            path = path / ref.key
        elif ref.key != "value":
            raise SecretMissing(ref)
        try:
            fd = os.open(path, _FLAGS)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise SecretDenied(ref, "is a symlink") from None
            missing = exc.errno in {errno.ENOENT, errno.ENOTDIR}
            raise SecretMissing(ref) if missing else SecretDenied(ref, "unreadable") from None
        try:
            st = os.fstat(fd)
            self._check(ref, st, "file")
            return (self._value(ref, self._read(ref, fd)) if read else b""), st
        finally:
            os.close(fd)

    def _check(self, ref: SecretRef, st: os.stat_result, what: str) -> None:
        if what == "file" and not stat.S_ISREG(st.st_mode):
            raise SecretDenied(ref, "not a regular file")
        if what != "file" and not stat.S_ISDIR(st.st_mode):
            raise SecretDenied(ref, f"{what} is not a directory")
        problem = None if self.allow_loose_permissions else check_private_mode(st, kind=what)
        if problem:
            raise SecretDenied(ref, problem)

    def _read(self, ref: SecretRef, fd: int) -> bytes:
        data = b""
        try:
            while len(data) <= self.max_bytes and (chunk := os.read(fd, self.max_bytes + 1 - len(data))):
                data += chunk
        except OSError:
            raise SecretDenied(ref, "unreadable") from None
        if len(data) > self.max_bytes:
            raise SecretDenied(ref, "too large")
        return data

    @staticmethod
    def _value(ref: SecretRef, data: bytes) -> bytes:
        """The stored bytes as a value: one line end stripped, no control byte, not empty, UTF-8."""
        value = clean_secret_bytes(ref, data)
        if not value:
            raise SecretDenied(ref, "is empty")
        try:
            value.decode("utf-8")
            return value
        except UnicodeDecodeError:
            pass
        # Raised outside the handler: the decode error holds the bytes and must not be the context.
        raise SecretDenied(ref, "is not utf-8")
