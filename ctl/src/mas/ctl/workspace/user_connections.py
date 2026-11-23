#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Load ``$XDG_CONFIG_HOME/mas/connections.yaml``. Ctl only."""

from __future__ import annotations

from typing import Any

import click
from mas.runtime.constants import CONNECTIONS_CONFIG_FILENAME
from mas.runtime.secrets import load_checked_document
from mas.runtime.xdg import mas_config_dir


class ConnectionsFileError(click.ClickException):
    """The connections file did not load or was refused. The message names the file and the problem,
    never a line or a value of it; a command reports it in one line and exits 1."""


def load_user_connections() -> dict[str, Any]:
    """The user connections file as a mapping. Empty when the file is absent.

    Refused (:class:`ConnectionsFileError`) when it is not a YAML mapping, or holds a literal secret or a
    credential in a URL or host.
    """
    path = mas_config_dir() / CONNECTIONS_CONFIG_FILENAME
    if not path.is_file():
        return {}
    try:
        return load_checked_document(path)
    except ValueError as exc:  # YamlLoadError and SecretRefError are ValueErrors
        raise ConnectionsFileError(str(exc)) from None
