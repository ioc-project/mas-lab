#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The requirement grammar: a small vocabulary the identity gate evaluates over a scheme's public facts.

The identity contract does not define it: ``PublicView.facts`` is opaque there. This module belongs to the identity
gate; a scheme with its own vocabulary puts what a policy needs in its facts and the gate stays generic.

A requirement is a mapping: ``{"fact": <name>, <operator>: <operand>, ..., "label": <slug>}``. ``fact`` is a key of
``PublicView.facts``; a dotted name (``limits.calls``) walks nested mappings. Operators, all ANDed:

==================  =====================================================================
``equals``          the fact equals the operand
``in``              the fact is a member of the operand list
``contains``        the fact is a list (or text) that contains the operand
``min``             the fact is a number >= the operand
``max``             the fact is a number <= the operand
``exists``          ``true``: the fact is present; ``false``: it is absent
``fresh_within_s``  the fact is an epoch timestamp at most N seconds older than the clock
==================  =====================================================================

``label`` (optional) is a short name of the requirement for the block reason (``scope_uncovered:<label>``): letters,
digits and ``_ . : -``, at most 40 characters; without it the reason names the fact. It is a name the policy author
chose, never a value.

A gate also lets a requirement read the acting principal, as facts under ``principal.``: ``principal.chain`` (the
delegation chain, nearest actor first, a list of ids: ``contains`` asks whether an actor is in it),
``principal.chain_depth`` (its length: ``max`` bounds how far a request was delegated) and ``principal.actor``. They
come from the bound principal, never from the scheme's view, and a scheme fact of the same name is replaced. An
unknown chain is absent (``fact_missing``), never "direct": a requirement on it fails closed.

Timestamps use the same operators: ``not_before`` is ``min``, ``not_after`` is ``max``. Freshness needs the
clock (``now``), which the caller passes. The result is ``None`` (satisfied) or a short reason
code: ``fact_missing:<fact>``, ``fact_mismatch:<fact>``, ``fact_not_in:<fact>`` (also a ``contains`` that does not
hold), ``fact_not_number:<fact>``,
``fact_below_min:<fact>``, ``fact_above_max:<fact>``, ``fact_present:<fact>``, ``fact_stale:<fact>``,
``fact_in_future:<fact>``, or ``invalid`` (no ``fact``, an unknown operator, an operand of the wrong type, or
``fresh_within_s`` without a clock). A reason holds a fact **name**, never a fact or operand value.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mas.library.standard.lib.identity.contract import PublicView

OPERATORS: frozenset[str] = frozenset({"equals", "in", "contains", "min", "max", "exists", "fresh_within_s"})
#: A timestamp this far ahead of the clock is not "fresh": it is wrong.
MAX_FUTURE_SKEW = 60.0
LABEL_MAX = 40
_LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,%d}$" % LABEL_MAX)
#: The prefix of the facts a gate takes from the acting principal rather than from a scheme's public view.
PRINCIPAL_PREFIX = "principal."
_ABSENT = object()


def reason_name(requirement: Mapping[str, Any]) -> str:
    """The name a block reason uses for ``requirement``: its ``label``, else its fact name."""
    label = requirement.get("label")
    return label if isinstance(label, str) and _LABEL.match(label) else _slug(str(requirement.get("fact", "")))


def reads_principal(requirement: Mapping[str, Any]) -> bool:
    """Does ``requirement`` read the acting principal (a fact under ``principal.``) rather than the public view?"""
    name = requirement.get("fact")
    return isinstance(name, str) and name.startswith(PRINCIPAL_PREFIX)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_.:-]", "_", name.lower())[:40] or "fact"


def _lookup(facts: Mapping[str, Any], name: str) -> Any:
    current: Any = facts
    for part in name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _ABSENT
        current = current[part]
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate_requirement(requirement: Mapping[str, Any], view: PublicView, *, now: float | None = None) -> str | None:
    """``None`` when ``view.facts`` satisfy ``requirement``, else a value-free reason code.

    ``now`` is the broker clock (epoch seconds); ``fresh_within_s`` needs it and is ``invalid`` without it.
    ``not_after`` on a timestamp fact is ``max``, ``not_before`` is ``min``.
    """
    if not isinstance(requirement, Mapping):
        return "invalid"
    name = requirement.get("fact")
    if not isinstance(name, str) or not name:
        return "invalid"
    if "label" in requirement and not (isinstance(requirement["label"], str) and _LABEL.match(requirement["label"])):
        return "invalid"
    operators = [key for key in requirement if key not in ("fact", "label")]
    if not operators or any(key not in OPERATORS for key in operators):
        return "invalid"
    tag = _slug(name)
    value = _lookup(view.get("facts", {}), name)
    for op in operators:
        operand = requirement[op]
        if op == "exists":
            if not isinstance(operand, bool):
                return "invalid"
            if operand and value is _ABSENT:
                return f"fact_missing:{tag}"
            if not operand and value is not _ABSENT:
                return f"fact_present:{tag}"
            continue
        if op == "in" and not isinstance(operand, list):
            return "invalid"
        if op in ("min", "max") and not _is_number(operand):
            return "invalid"
        if op == "fresh_within_s" and (not _is_number(operand) or operand <= 0 or now is None):
            return "invalid"
        if value is _ABSENT:
            return f"fact_missing:{tag}"
        if op == "equals" and value != operand:
            return f"fact_mismatch:{tag}"
        if op == "in" and value not in operand:
            return f"fact_not_in:{tag}"
        if op == "contains":
            held = isinstance(value, (list, tuple)) or (isinstance(value, str) and isinstance(operand, str))
            if not (held and operand in value):
                return f"fact_not_in:{tag}"
        if op == "fresh_within_s":
            if not _is_number(value):
                return f"fact_not_number:{tag}"
            assert now is not None
            if value > now + MAX_FUTURE_SKEW:
                return f"fact_in_future:{tag}"
            if now - value > operand:
                return f"fact_stale:{tag}"
        if op in ("min", "max"):
            if not _is_number(value):
                return f"fact_not_number:{tag}"
            if op == "min" and value < operand:
                return f"fact_below_min:{tag}"
            if op == "max" and value > operand:
                return f"fact_above_max:{tag}"
    return None


class GateConfigError(ValueError):
    """A requirement is malformed. The message names the requirement and the rule, never a value."""


@dataclass(frozen=True)
class Requirement:
    """What one tool needs from the identity of the agent that calls it: facts of its public view that must hold."""

    tool: str
    identity: str
    ref: str
    require: tuple[Mapping[str, Any], ...] = ()

    @property
    def needs_facts(self) -> bool:
        """Does a requirement read the scheme's public view (so the scheme needs ``describe``)?"""
        return any(not reads_principal(check) for check in self.require)


_REQUIREMENT_KEYS = frozenset({"identity", "task", "audience", "require"})


def parse_requirements(raw: Any) -> dict[str, Requirement]:
    """Validate a gate's ``requirements`` and return one :class:`Requirement` per tool name.

    ``require`` is a list of requirements in the grammar of this module, all of which must hold; ``identity`` names the
    record the requirement reads (absent: the one record the agent holds); ``task`` or ``audience`` is the reference
    the public view is asked for.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise GateConfigError("requirements must be a mapping of tool name to requirement")
    out: dict[str, Requirement] = {}
    for tool, item in raw.items():
        if not isinstance(tool, str) or not tool:
            raise GateConfigError("requirements: a tool name is required")
        if not isinstance(item, Mapping):
            raise GateConfigError(f"requirement {tool!r} must be a mapping")
        unknown = sorted(set(item) - _REQUIREMENT_KEYS)
        if unknown:
            raise GateConfigError(f"requirement {tool!r}: unknown key {unknown[0]!r}")
        for key in ("identity", "task", "audience"):
            if key in item and (not isinstance(item[key], str) or not item[key]):
                raise GateConfigError(f"requirement {tool!r}: {key} must be a non-empty string")
        require = item.get("require", [])
        if not isinstance(require, list):
            raise GateConfigError(f"requirement {tool!r}: require is a list of requirements")
        for check in require:
            # a malformed one is a config error, not a block at decision time
            if evaluate_requirement(check, PublicView(facts={}), now=0.0) == "invalid":
                raise GateConfigError(f"requirement {tool!r}: a requirement needs a fact and grammar operators")
        out[tool] = Requirement(
            tool=tool,
            identity=str(item.get("identity") or ""),
            ref=str(item.get("task") or item.get("audience") or ""),
            require=tuple(dict(c) for c in require),
        )
    return out


def unmet(requirement: Requirement, view: PublicView, *, now: float) -> str:
    """The name (the ``label``, else the fact) of the first requirement the facts do not cover, or ``""``."""
    for check in requirement.require:
        if evaluate_requirement(check, view, now=now) is not None:
            return reason_name(check)
    return ""


def principal_facts(principal: Mapping[str, Any]) -> dict[str, Any]:
    """The facts a requirement reads under ``principal.``: the actor, the delegation chain and its depth.

    An absent chain stays absent (unknown is not "direct").
    """
    facts: dict[str, Any] = {}
    if principal.get("actor"):
        facts["actor"] = principal["actor"]
    chain = principal.get("chain")
    if isinstance(chain, (list, tuple)):
        facts["chain"] = [str(item) for item in chain]
        facts["chain_depth"] = len(chain)
    return facts
