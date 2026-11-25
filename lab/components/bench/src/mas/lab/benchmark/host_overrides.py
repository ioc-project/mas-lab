#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""What a host (the CLI, the controller) handed this process, carried to run workers.

A spawned run worker is a fresh process: it never read the CLI's environment and
inherits no module state. The parent snapshots the explicit overrides it holds
into the job (:func:`capture`; the host's environment itself is inherited by the
spawned process) and the worker installs them for the run (:func:`apply`). Nothing here reads an environment variable.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import Any


def capture() -> dict[str, Any]:
    """The overrides this process holds, as plain data for a job payload."""
    from mas.ctl.workspace.config import bound_infra_refs
    from mas.library.standard.lib.llm_scheduler import current_overrides
    from mas.runtime import hostenv
    from mas.runtime.engine.llm_cache import snapshot_paths

    return {
        "overlay": hostenv.overlay_snapshot(),
        "scheduler": current_overrides(),
        "workspace_infra_refs": bound_infra_refs(),
        "llm_cache": snapshot_paths(),
    }


@contextmanager
def apply(host: Mapping[str, Any] | None) -> Iterator[None]:
    """Install the overrides of :func:`capture` for the duration of a run."""
    if not host:
        yield
        return
    from mas.ctl.workspace.config import bind_infra_refs
    from mas.library.standard.lib import llm_scheduler
    from mas.runtime import hostenv
    from mas.runtime.engine.llm_cache import bind_paths

    previous = llm_scheduler.explicit_overrides()
    cache = dict(host.get("llm_cache") or {})
    with ExitStack() as stack:
        stack.enter_context(hostenv.overlay(dict(host.get("overlay") or {})))
        llm_scheduler.install_overrides(dict(host.get("scheduler") or {}))
        stack.callback(llm_scheduler.install_overrides, previous)
        stack.enter_context(bind_infra_refs(host.get("workspace_infra_refs")))
        stack.enter_context(bind_paths(cli=cache.get("cli"), default=cache.get("default")))
        yield
