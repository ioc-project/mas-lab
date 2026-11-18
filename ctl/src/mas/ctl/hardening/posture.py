#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Which posture a deployment selected, what it waived, and what that makes of a failed check.

The selection lives in the Flavour, the one document that says how a deployment differs from a
laptop run::

    spec:
      hardening:
        profile: production        # default: omitted
        waive:
          - check: plain-http-remote
            reason: the lab gateway is on the cluster network

Omitted, nothing here runs and no session changes. ``production`` refuses to start when a check
fails and no waiver covers it. A waiver always names its reason, and the report lists it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mas.ctl.hardening.checks import BY_ID, CHECKS
from mas.ctl.hardening.model import (
    DEFAULT,
    DEFERRED,
    FAILED,
    PASSED,
    POSTURES,
    PRODUCTION,
    RUNTIME,
    WAIVED,
    HardeningError,
    HardeningInput,
    Report,
    Result,
    Waiver,
)

logger = logging.getLogger(__name__)


def posture_of(flavour_spec: dict[str, Any] | None) -> tuple[str, tuple[Waiver, ...]]:
    """``(posture, waivers)`` a Flavour spec selects; a malformed block is a ``HardeningError``."""
    block = (flavour_spec or {}).get("hardening")
    if block is None:
        return DEFAULT, ()
    if not isinstance(block, dict):
        raise HardeningError("spec.hardening must be a mapping with profile and waive")
    unknown = sorted(set(block) - {"profile", "waive"})
    if unknown:
        raise HardeningError(f"spec.hardening has unknown keys {unknown}; use profile and waive")
    posture = str(block.get("profile") or DEFAULT).strip().lower()
    if posture not in POSTURES:
        raise HardeningError(f"spec.hardening.profile {posture!r} is not one of {list(POSTURES)}")
    return posture, _waivers(block.get("waive"))


def _waivers(raw: Any) -> tuple[Waiver, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise HardeningError("spec.hardening.waive must be a list of {check, reason}")
    out: list[Waiver] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"check", "reason"}:
            raise HardeningError("a hardening waiver is exactly {check, reason}")
        check, reason = str(item["check"]).strip(), str(item["reason"]).strip()
        if check not in BY_ID:
            raise HardeningError(f"hardening waiver names unknown check {check!r}; known: {sorted(BY_ID)}")
        if not reason:
            raise HardeningError(f"hardening waiver for {check!r} needs a reason")
        out.append(Waiver(check, reason))
    return tuple(out)


def evaluate(inp: HardeningInput, posture: str, waivers: tuple[Waiver, ...] = ()) -> Report:
    """Run every check. A failed check with a waiver is ``waived``; a runtime check is ``deferred``."""
    reasons = {waiver.check: waiver.reason for waiver in waivers}
    results: list[Result] = []
    for check in CHECKS:
        problems = tuple(check.evaluate(inp))
        reason = reasons.get(check.id, "")
        if problems:
            outcome = WAIVED if reason else FAILED
        else:
            outcome = DEFERRED if check.scope == RUNTIME else PASSED
        results.append(Result(check.id, check.severity, check.scope, outcome, problems, check.fix, reason))
    return Report(posture, tuple(results))


def enforce(report: Report) -> None:
    """Production refuses a report with a failed check; the default posture only logs it."""
    if report.ok:
        # a waiver is an explicit override of a check: it is always visible in the admission log
        (logger.warning if report.waived else logger.info)(report.summary())
        return
    if report.posture != PRODUCTION:
        logger.warning(report.summary())
        return
    lines = [f"production hardening refused to start: {len(report.failed)} check(s) failed"]
    for result in report.failed:
        lines.append(f"  [{result.severity}] {result.check}: {'; '.join(result.problems)}")
        lines.append(f"      fix: {result.fix}")
    lines.append("Fix them, or waive one in the Flavour: spec.hardening.waive: [{check, reason}].")
    raise HardeningError("\n".join(lines))


def apply_posture(flavour_spec: dict[str, Any] | None, build_input: Callable[[], HardeningInput]) -> Report | None:
    """The session-start gate: ``None`` and no work unless the Flavour selected ``production``."""
    posture, waivers = posture_of(flavour_spec)
    if posture != PRODUCTION:
        return None
    report = evaluate(build_input(), posture, waivers)
    enforce(report)
    return report


_PLUGIN_NAMES = {"gov_hardening_profile", "hardening_profile"}


def with_report(spec: dict[str, Any], report: Report) -> dict[str, Any]:
    """``spec`` with the report handed to a declared ``gov_hardening_profile`` plugin as its ``report`` param.

    A spec that does not declare the plugin comes back unchanged. The plugin records the report at the
    first egress of the session.
    """
    governance = spec.get("governance")
    if not isinstance(governance, list):
        return spec
    records = [item.as_dict() for item in report.results]
    rewritten: list[Any] = []
    changed = False
    for entry in governance:
        if isinstance(entry, str) and entry in _PLUGIN_NAMES:
            entry, changed = {entry: {"report": records, "profile": report.posture}}, True
        elif isinstance(entry, dict) and len(entry) == 1 and next(iter(entry)) in _PLUGIN_NAMES:
            name = next(iter(entry))
            entry, changed = {name: {**(entry[name] or {}), "report": records, "profile": report.posture}}, True
        rewritten.append(entry)
    return {**spec, "governance": rewritten} if changed else spec
