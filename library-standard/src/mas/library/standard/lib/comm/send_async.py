#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Send through a link that may be async (``asend``) or still sync (``send``)."""

from __future__ import annotations

from typing import Any

from mas.runtime.aio import run_sync, warn_sync_plugin


def can_send(link: Any) -> bool:
    return callable(getattr(link, "asend", None)) or callable(getattr(link, "send", None))


async def asend_via(link: Any, *args: Any, **kwargs: Any) -> Any:
    """Await ``link.asend``. A link that only has ``send`` runs it on the worker threads and warns once."""
    asend = getattr(link, "asend", None)
    if callable(asend):
        return await asend(*args, **kwargs)
    warn_sync_plugin(link, "send", "asend")
    return await run_sync(link.send, *args, **kwargs)


__all__ = ["asend_via", "can_send"]
