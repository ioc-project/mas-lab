#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Rewrite the removed ``*_env`` keys and ``env:`` strings of a YAML file into SecretRefs and plain values.

The rewrite is line based, so comments, order and quoting of every other line stay as they are. It handles the
forms the old files used:

- ``api_key_env: NAME`` becomes ``api_key: {secret: <secret>}`` (the same for ``secret_env``, ``password_env``,
  ``embed_api_key_env``); ``<secret>`` is the name ``mas-ctl secrets import-env`` gives a known variable
  (``OPENAI_API_KEY`` is ``openai``), else the variable name in lower case with ``-``;
- ``key: env:NAME|default`` becomes ``key: default`` (a host);
- ``key: env:NAME`` with no default, and any other ``*_env`` key, are listed and left alone.

The value of a variable is never read: the caller stores each secret with ``mas-ctl secrets set``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mas.runtime.secrets.env_names import KNOWN_ENV_NAMES

_SECRET_KEYS = ("api_key", "secret", "password", "embed_api_key")
_KEY_ENV = re.compile(
    r"^(?P<indent>\s*(?:-\s+)?)(?P<key>[A-Za-z0-9_.-]+)_env:\s*(?P<value>[^\s#]+)\s*(?P<comment>#.*)?$"
)
_ENV_STRING = re.compile(
    r"^(?P<head>\s*(?:-\s+)?[A-Za-z0-9_.-]+:\s*)(?P<quote>[\"']?)env:(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\|(?P<default>[^\"'#]*?))?(?P=quote)\s*(?P<comment>#.*)?$"
)


@dataclass
class Migration:
    """The outcome for one file."""

    text: str
    secrets: dict[str, str] = field(default_factory=dict)  # secret name -> the variable it replaces
    left: list[str] = field(default_factory=list)  # lines that need a person
    changed: int = 0


def secret_name(variable: str) -> str:
    """The secret a variable becomes: the known name, else the variable in lower case."""
    return KNOWN_ENV_NAMES.get(variable) or re.sub(r"[^a-z0-9.-]+", "-", variable.lower()).strip("-")


def migrate_text(text: str) -> Migration:
    """Rewrite *text*; every line that is not an old form is returned unchanged."""
    out = Migration(text)
    lines: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        new = _migrate_line(line, number, out)
        lines.append(new)
    out.text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return out


def _migrate_line(line: str, number: int, out: Migration) -> str:
    if line.lstrip().startswith("#"):
        return line
    key_env = _KEY_ENV.fullmatch(line)
    if key_env:
        key = key_env["key"]
        variable = key_env["value"].strip("\"'")
        if key not in _SECRET_KEYS:
            out.left.append(f"line {number}: {key}_env names a variable that is not a secret; write its value")
            return line
        name = secret_name(variable)
        out.secrets[name] = variable
        out.changed += 1
        comment = f"  {key_env['comment']}" if key_env["comment"] else ""
        return f"{key_env['indent']}{key}: {{secret: {name}}}{comment}"
    env_string = _ENV_STRING.fullmatch(line)
    if env_string:
        default = env_string["default"]
        if default is None or not default.strip():
            out.left.append(f"line {number}: env:{env_string['name']} has no default; write the value")
            return line
        out.changed += 1
        comment = f"  {env_string['comment']}" if env_string["comment"] else ""
        return f"{env_string['head']}{default.strip()}{comment}"
    return line


def migrate_file(path: Path, *, write: bool) -> Migration:
    """Migrate one file; with ``write`` the file is replaced, with a ``.bak`` copy next to it."""
    original = path.read_text(encoding="utf-8")
    result = migrate_text(original)
    if write and result.changed:
        path.with_name(path.name + ".bak").write_text(original, encoding="utf-8")
        path.write_text(result.text, encoding="utf-8")
    return result


def has_old_forms(text: str) -> bool:
    """Whether *text* still uses a removed form (used to tell a person, never to rewrite)."""
    result = migrate_text(text)
    return bool(result.changed or result.left)
