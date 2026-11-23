#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The run-time half of the production hardening profile.

``mas-ctl`` judges a session before it starts (``docs/references/hardening.md``). This plugin judges what
only exists at run time: the hosts a tool call reaches. It records the start-of-session report once, and
under ``profile: production`` it BLOCKs a tool call whose arguments carry a url to a host that is not on
``allowed_hosts``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from mas.runtime.boundary.gov.policy import EgressIntentView
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision

PLUGIN_ID = "gov_hardening_profile"
CHECK_ID = "egress-host-allowlist"
_SCHEMES = {"http", "https", "ws", "wss"}
# A tool call can carry a large argument tree; the walk is bounded so a hostile one cannot stall the gate.
_MAX_STRINGS = 256
_MAX_DEPTH = 8


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item, depth + 1)


def hosts_in(arguments: dict | None) -> list[str]:
    """The lower-case hosts of the http(s)/ws(s) urls found in ``arguments``, in order."""
    out: list[str] = []
    for index, text in enumerate(_strings(arguments or {})):
        if index >= _MAX_STRINGS:
            break
        try:
            parts = urlsplit(text.strip())
            host = parts.hostname
        except ValueError:
            continue
        if parts.scheme.lower() in _SCHEMES and host:
            out.append(host.lower())
    return out


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    """``host`` is listed, or sits under a listed ``*.suffix``. The comparison is on the whole host."""
    return any(
        host == item or (item.startswith("*.") and host.endswith(item[1:]) and host != item[2:]) for item in allowed
    )


def _hosts(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("allowed_hosts must be a non-empty list; an empty list is refused, not read as open or closed")
    hosts = tuple(str(item).strip().lower() for item in raw)
    if any(not item or item == "*" or "/" in item or ":" in item for item in hosts):
        raise ValueError("allowed_hosts entries are host names or *.suffix, no scheme, port, path or bare *")
    return hosts


class HardeningProfilePlugin:
    """Record the hardening report once; under ``production`` BLOCK a tool call to an unlisted host.

    ``profile``: ``production`` (default when the plugin is declared) or ``default`` (records only).
    ``allowed_hosts``: host names or ``*.suffix``; omitted, no host is judged. ``waive``: a reason for
    ``egress-host-allowlist``, which turns the BLOCK into a recorded ALLOW. ``report``: set by mas-ctl.
    """

    plugin_id = "gov_hardening_profile@v1"

    def __init__(
        self,
        profile: str = "production",
        allowed_hosts: list[str] | None = None,
        waive: list[dict[str, str]] | None = None,
        report: list[dict[str, Any]] | None = None,
    ) -> None:
        if profile not in {"production", "default"}:
            raise ValueError(f"profile must be production or default, got {profile!r}")
        self.profile = profile
        self.allowed_hosts = _hosts(allowed_hosts)
        self.waivers = {str(item["check"]): str(item["reason"]) for item in (waive or [])}
        self.report = list(report or [])
        self._recorded = False

    def _summary(self) -> str:
        counts: dict[str, list[str]] = {"passed": [], "failed": [], "waived": [], "deferred": []}
        for item in self.report:
            counts.setdefault(str(item.get("outcome")), []).append(str(item.get("check")))
        parts = ", ".join(f"{name} {','.join(sorted(ids)) or '-'}" for name, ids in counts.items())
        listed = f"{len(self.allowed_hosts)} allowed host(s)" if self.allowed_hosts else "no host allow-list"
        waived = f"; waived {CHECK_ID}: {self.waivers[CHECK_ID]}" if CHECK_ID in self.waivers else ""
        return f"hardening {self.profile}: {parts}; {listed}{waived}"

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig):
        decision, plugin_id, reason = self._judge(intent)
        if self._recorded:
            return decision, plugin_id, reason
        self._recorded = True
        if decision is GovDecision.ALLOW:
            return GovDecision.LOG, PLUGIN_ID, self._summary()
        return decision, plugin_id, f"{reason}; {self._summary()}"

    def _judge(self, intent: EgressIntentView):
        if intent.op != "TOOL_CALL" or not self.allowed_hosts:
            return GovDecision.ALLOW, PLUGIN_ID, "no host to judge on this call"
        unlisted = sorted({h for h in hosts_in(intent.tool_arguments) if not host_allowed(h, self.allowed_hosts)})
        if not unlisted:
            return GovDecision.ALLOW, PLUGIN_ID, "every host of the call is on the allow-list"
        reason = f"tool call reaches {len(unlisted)} host(s) not on allowed_hosts"
        if CHECK_ID in self.waivers:
            return GovDecision.LOG, PLUGIN_ID, f"{reason}; waived: {self.waivers[CHECK_ID]}"
        if self.profile != "production":
            return GovDecision.LOG, PLUGIN_ID, f"{reason}; the default posture records it"
        return GovDecision.BLOCK, PLUGIN_ID, f"{reason}; the production posture blocks it"
