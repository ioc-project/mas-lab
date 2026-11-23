#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Data of the hardening profile: a check, what it reads, and what it reports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT = "default"
PRODUCTION = "production"
POSTURES = (DEFAULT, PRODUCTION)

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
SEVERITIES = (CRITICAL, HIGH, MEDIUM)

STATIC = "static"
RUNTIME = "runtime"

PASSED = "passed"
FAILED = "failed"
WAIVED = "waived"
DEFERRED = "deferred"


class HardeningError(RuntimeError):
    """The production posture refuses to start: at least one check failed and none waived it."""


@dataclass(frozen=True)
class Exposure:
    """A listener this session would start, and whether it verifies who calls it.

    ``identity`` is ``verified`` (a non-empty admission policy), ``empty`` (a policy that names no
    one) or ``none``.
    """

    name: str
    protocol: str
    url: str
    host: str
    identity: str

    @property
    def loopback(self) -> bool:
        from mas.runtime.secrets import is_loopback_host

        return is_loopback_host(self.host)


@dataclass(frozen=True)
class HardeningInput:
    """What the checks read: the resolved session, never the files it came from."""

    # agent id -> effective agent spec (Control lists applied)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    exposures: tuple[Exposure, ...] = ()
    # ``infra resolved`` view: one entry per destination, with its pipeline ``steps``
    llm: tuple[dict[str, Any], ...] = ()
    tool: tuple[dict[str, Any], ...] = ()
    # tool server rows (id -> row)
    tool_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    # every address the session connects to, as (where, url)
    urls: tuple[tuple[str, str], ...] = ()
    # every settings mapping a transport or a layer reads, as (where, mapping)
    settings: tuple[tuple[str, dict[str, Any]], ...] = ()
    engine_cache: dict[str, Any] = field(default_factory=dict)
    secret_store: str = "file"
    allow_loose_permissions: bool = False


@dataclass(frozen=True)
class Check:
    id: str
    severity: str
    scope: str
    rationale: str
    fix: str
    evaluate: Callable[[HardeningInput], list[str]]


@dataclass(frozen=True)
class Waiver:
    check: str
    reason: str


@dataclass(frozen=True)
class Result:
    check: str
    severity: str
    scope: str
    outcome: str
    problems: tuple[str, ...] = ()
    fix: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"check": self.check, "severity": self.severity, "scope": self.scope}
        out["outcome"] = self.outcome
        if self.problems:
            out["problems"] = list(self.problems)
        if self.fix and self.outcome == FAILED:
            out["fix"] = self.fix
        if self.reason:
            out["waiver_reason"] = self.reason
        return out


@dataclass(frozen=True)
class Report:
    posture: str
    results: tuple[Result, ...]

    def with_outcome(self, outcome: str) -> list[Result]:
        return [item for item in self.results if item.outcome == outcome]

    @property
    def failed(self) -> list[Result]:
        return self.with_outcome(FAILED)

    @property
    def waived(self) -> list[Result]:
        return self.with_outcome(WAIVED)

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "ok": self.ok,
            "passed": len(self.with_outcome(PASSED)),
            "failed": len(self.failed),
            "waived": len(self.waived),
            "deferred": len(self.with_outcome(DEFERRED)),
            "results": [item.as_dict() for item in self.results],
        }

    def summary(self) -> str:
        """One line a log or a governance record can carry."""
        names = lambda items: ",".join(sorted(item.check for item in items)) or "-"  # noqa: E731
        return (
            f"hardening {self.posture}: passed {len(self.with_outcome(PASSED))}, failed {names(self.failed)}, "
            f"waived {names(self.waived)}, deferred {names(self.with_outcome(DEFERRED))}"
        )
