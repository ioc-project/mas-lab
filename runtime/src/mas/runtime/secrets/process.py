#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Child process environment: an allowlist, never the host environment."""

from __future__ import annotations

from collections.abc import Mapping

from mas.runtime import hostenv
from mas.runtime.envkeys import CHILD_KEYS, LAB_KEYS, PROCESS_KEEP


class HostNotInstalled(RuntimeError):
    """A child was about to start in a process whose entry point installed no host environment."""


def child_env(extra: Mapping[str, str] | None = None, *, lab: bool = False) -> dict[str, str]:
    """Environment for a spawn: the host's :data:`~mas.runtime.envkeys.CHILD_KEYS` (run, trust anchors,
    proxies; a credentialed proxy URL is refused at the entry point), with *lab* also
    :data:`~mas.runtime.envkeys.LAB_KEYS` and :data:`~mas.runtime.envkeys.PROCESS_KEEP`, plus *extra*.

    The source is hostenv: a process whose entry point installed no host raises instead of starting a
    child with an empty environment.
    """
    if not hostenv.installed():
        raise HostNotInstalled(
            "no host environment is installed in this process, so a child would start with an empty "
            "environment. The process's entry point must call "
            "mas.runtime.entrypoint.install_host_environment() first."
        )
    host = hostenv.snapshot()
    keys = CHILD_KEYS | LAB_KEYS | PROCESS_KEEP if lab else CHILD_KEYS
    out = {key: host[key] for key in sorted(keys) if key in host}
    out.update({str(key): str(value) for key, value in (extra or {}).items()})
    return out
