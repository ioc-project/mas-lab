#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Scoped control-API actor for environment steps."""

from __future__ import annotations

from typing import Any


class EnvironmentControlAdapter:
    """Forwards world-to-session actions through a ControlContract.

    Actor is ``environment``, surface is ``plugin``. Methods the bound
    contract does not expose are refused. Keyword names match ControlContract.
    """

    actor = "environment"
    surface = "plugin"

    def __init__(self, contract: object) -> None:
        self._contract = contract

    def _method(self, name: str) -> Any:
        method = getattr(self._contract, name, None)
        if not callable(method):
            raise PermissionError(f"environment control cannot {name}: capability refused")
        return method

    def steer(self, session_id: str, *, text: str = "", **kwargs: Any) -> Any:
        return self._method("steer")(session_id, text=text, **kwargs)

    def enqueue_input(self, session_id: str, *, text: str, source: str = "environment", **kwargs: Any) -> Any:
        return self._method("enqueue_input")(session_id, text=text, source=source, **kwargs)

    def pause(self, session_id: str, *, reason: str = "environment", **kwargs: Any) -> Any:
        return self._method("pause")(session_id, reason=reason, **kwargs)

    def resume(self, session_id: str, **kwargs: Any) -> Any:
        return self._method("resume")(session_id, **kwargs)

    def snapshot(self, session_id: str, *, label: str = "", **kwargs: Any) -> Any:
        return self._method("snapshot")(session_id, label=label, **kwargs)

    def persist(self, session_id: str, *, label: str = "", auto_stop: bool = False, **kwargs: Any) -> Any:
        return self._method("persist")(session_id, label=label, auto_stop=auto_stop, **kwargs)

    def cancel_inflight(self, session_id: str, **kwargs: Any) -> Any:
        del kwargs
        return self._method("cancel_inflight")(session_id)

    def disable_tool(self, session_id: str, *, name: str, reason: str = "environment", **kwargs: Any) -> Any:
        return self._method("disable_tool")(session_id, name=name, reason=reason, **kwargs)

    def enable_tool(self, session_id: str, *, name: str, reason: str = "environment", **kwargs: Any) -> Any:
        return self._method("enable_tool")(session_id, name=name, reason=reason, **kwargs)
