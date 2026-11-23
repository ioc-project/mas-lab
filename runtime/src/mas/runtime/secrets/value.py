#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Opaque secret value. Dump paths never show the bytes."""

from __future__ import annotations

from typing import Any

import yaml
from mas.runtime.secrets.errors import SecretDenied
from mas.runtime.secrets.ref import SecretRef

_PLACEHOLDER = "<secret:{name}>"
_FORBIDDEN = frozenset(range(0x20)) | {0x7F}


def clean_secret_bytes(ref: SecretRef, data: bytes) -> bytes:
    """Strip one trailing line end; refuse any other control byte.

    A control byte in a credential breaks the header or URL it goes into, and the
    HTTP client then echoes the whole value in its error text. The refusal names the ref only.
    """
    if data.endswith(b"\r\n"):
        data = data[:-2]
    elif data.endswith(b"\n"):
        data = data[:-1]
    if any(byte in _FORBIDDEN for byte in data):
        del data
        raise SecretDenied(ref, "is not a valid secret value")
    return data


class SecretValue:
    """Wraps str/bytes. ``reveal()`` is the only way to read the value."""

    __slots__ = ("_data", "_name")
    __hash__ = None  # type: ignore[assignment]

    def __init__(self, data: str | bytes, *, name: str = "") -> None:
        if isinstance(data, bytes):
            self._data = data
        else:
            self._data = str(data).encode("utf-8")
        self._name = str(name or "value")

    def reveal(self) -> str:
        try:
            return self._data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        # Raised outside the handler: the decode error holds the bytes and must not be the context.
        raise SecretDenied(SecretRef(name=self._name), "is not utf-8") from None

    def reveal_bytes(self) -> bytes:
        return self._data

    def _label(self) -> str:
        return _PLACEHOLDER.format(name=self._name)

    def __repr__(self) -> str:
        return self._label()

    def __str__(self) -> str:
        return self._label()

    def __format__(self, format_spec: str) -> str:
        return format(self._label(), format_spec)

    def __bool__(self) -> bool:
        return bool(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __deepcopy__(self, memo: dict[int, Any]) -> SecretValue:
        return self

    def __getstate__(self) -> Any:  # object's default would return the slots, bytes included
        raise TypeError(f"{self._label()} has no state to export")

    def __reduce__(self) -> tuple[type[str], tuple[str]]:
        return (str, (self._label(),))


def _represent_secret_value(dumper: Any, data: SecretValue) -> Any:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data))


def _represent_secret_ref(dumper: Any, data: SecretRef) -> Any:
    return dumper.represent_data(data.to_manifest())


# SafeDumper writes SecretValue as ``<secret:NAME>`` and SecretRef as a mapping.
yaml.SafeDumper.add_representer(SecretValue, _represent_secret_value)
yaml.SafeDumper.add_representer(SecretRef, _represent_secret_ref)
