#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a console entry point calls first: the host environment, filtered, then a scrubbed process.

Every entry point (``mas-ctl``, ``mas-lab``, the controller daemon and API, ``mas-mcp``, ``mas-slim``,
``mas-a2a``, the ``python -m`` services) calls :func:`install_host_environment` before anything else:
the declared host keys go into :mod:`mas.runtime.hostenv` and ``os.environ`` is reduced to
``child_env(lab=True)``, so a child spawned later, or a library that reads ``os.environ`` itself,
finds no secret. This module and the test harness are the only runtime code that reads ``os.environ``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from mas.runtime import hostenv
from mas.runtime.secrets.process import child_env

# Switches that keep a third-party library from loading a ``.env`` file (it holds secrets) into
# ``os.environ`` when imported (deepeval's autoload, litellm in DEV mode, python-dotenv's ``load_dotenv``).
# Set whatever the host said.
THIRD_PARTY_PINS = {
    "DEEPEVAL_DISABLE_DOTENV": "1",
    "LITELLM_MODE": "PRODUCTION",
    "PYTHON_DOTENV_DISABLED": "1",
}


def _replace_process_environment(clean: Mapping[str, str]) -> None:
    os.environ.clear()
    os.environ.update(clean)
    os.environ.update(THIRD_PARTY_PINS)


def install_spawned_environment(allowed: Mapping[str, str]) -> None:
    """A spawned child process: the parent's allowlist becomes both hostenv and ``os.environ``."""
    clean = {str(key): str(value) for key, value in allowed.items()}
    _replace_process_environment(clean)
    hostenv.install(clean)


def install_host_environment() -> dict[str, str]:
    """Entry-point bootstrap: the declared host keys go into hostenv, then the process is scrubbed.

    A proxy or OTLP endpoint with credentials in its URL is refused. A process whose host is already
    installed (a test session, ``mas-lab`` running the daemon in the foreground) keeps its environment.
    Returns what hostenv holds, the overlay included.
    """
    if hostenv.installed():
        return hostenv.snapshot()
    snap = dict(os.environ)
    hostenv.refuse_credentialed_urls(snap)
    snap.setdefault("OBSERVE_REALTIME_OBSERVABILITY_ENABLED", "false")
    hostenv.install(snap)  # the one place a host environment enters library code
    del snap
    _replace_process_environment(child_env(lab=True))
    return hostenv.snapshot()
