#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Governance-block handlers — policy, HITL, budget, and control events.

A governance *decision* is a ``GovernanceEvent`` point span (``*.governance``)
carrying ``mas.governance.decision_type`` and ``mas.governance.policy_name``,
the attributes norm maps to ``decision`` and ``policyName``. The decision is
the one the event states (``decision_type``, ``decision``, ``outcome`` or a
HITL ``resolution``); an event that states none carries none.

The timing brackets of a check (``governance_authorize_*``,
``governance_validate_*``, ``obs_wrap_gov_*``, ``checkpoint_*``) are work, not
decisions: each pair is a ``ProcessingCall`` interval (``*.processing``) and,
when its records state an outcome, that outcome is a ``*.governance`` point
under it. A ProcessingCall is a core class, so the brackets are in the execution
export layer. A bracket runs in the agent visit, before or after the call it
checks (``checked_call_id``): its parent span is the visit's, and it carries the
State it reads (``ioa_observe.state.*``). The decision points are in the opt-in
governance layer (``ExportLayers.governance``).

Ontology block: ``governance`` (governance summand).
"""

from __future__ import annotations

from typing import Any, Dict

from mas.library.telemetry.conversion import semconv
from mas.library.telemetry.conversion.mappings.base import SpanEmitter, register


def _decision(ev: Dict[str, Any]) -> str:
    """The decision the event states, or ``""``."""
    for key in ("decision_type", "decision", "outcome", "resolution"):
        value = ev.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _policy_name(ev: Dict[str, Any]) -> str:
    """The policy the event names (its name, else the id it gives), or ``""``."""
    return str(ev.get("policy_name") or ev.get("policy_id") or ev.get("contract_id") or "")


def _governance_point(
    conv: SpanEmitter,
    ev: Dict[str, Any],
    default_kind: str,
    *,
    parent: str | None = None,
    **fields: Any,
) -> None:
    attrs: Dict[str, Any] = {"mas.governance.kind": ev.get("kind", default_kind)}
    decision = _decision(ev)
    if decision:
        attrs["mas.governance.decision_type"] = decision
    policy = _policy_name(ev)
    if policy:
        attrs["mas.governance.policy_name"] = policy
    if ev.get("reason") not in (None, ""):
        attrs["mas.governance.reason"] = str(ev["reason"])
    attrs.update({key: value for key, value in fields.items() if value not in (None, "")})
    if parent is None:
        conv.emit_annotation(ev, "GovernanceEvent", attrs)
        return
    # The decision a check bracket's end states: it annotates the bracket (its parent span).
    attrs.update({"mas.boundary": "GovernanceEvent", "mas.agent.id": conv.agent_id(ev)})
    payload = semconv.annotation_payload(ev, semconv.ANNOTATION_PAYLOAD_FIELDS)
    if payload:
        attrs["mas.annotation.content"] = payload
    conv.point_span("GovernanceEvent", attrs, parent, ts_ns=conv.ts_ns(ev))


@register("governance_event", "governance_checked")
def h_governance_event(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    _governance_point(
        conv,
        ev,
        "governance_checked",
        **{
            "mas.governance.hook": ev.get("hook"),
            "mas.governance.checks_passed": ev.get("checks_passed"),
        },
    )


@register("governance_policy", "transformation_event")
def h_governance_policy(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    details = ev.get("details") or {}
    _governance_point(
        conv,
        ev,
        "governance_policy",
        **{
            "mas.governance.trigger_point": ev.get("trigger_point"),
            "mas.governance.evaluation_mode": ev.get("evaluation_mode"),
            "mas.governance.action_taken": ev.get("action_taken"),
            "mas.governance.condition": details.get("condition"),
            "mas.governance.tool_filter": details.get("tool_filter"),
        },
    )


@register("hitl_request", "hitl_gate")
def h_hitl_request(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    """The question put to a human. It decides nothing; ``hitl_resolve`` does."""
    _governance_point(
        conv,
        ev,
        "hitl_request",
        **{
            "mas.governance.question": ev.get("question"),
            "mas.governance.auto_approve": ev.get("auto_approve"),
            "mas.governance.timeout_s": ev.get("timeout_s"),
        },
    )


@register("hitl_resolve")
def h_hitl_resolve(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    answer = ev.get("answer")
    _governance_point(
        conv,
        ev,
        "hitl_resolve",
        **{
            "mas.governance.answer": answer if isinstance(answer, (str, int, float, bool)) else conv.enc(answer),
            "mas.governance.operator_id": ev.get("operator_id"),
            "mas.governance.steering": ev.get("steering"),
        },
    )


@register("policy_denial", "policy_allow", "governance_denied")
def h_policy_event(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    kind = str(ev.get("kind") or "policy_denial")
    if not _decision(ev):
        # The kind itself is the decision these events report.
        ev = {**ev, "decision_type": "allow" if kind == "policy_allow" else "deny"}
    _governance_point(
        conv,
        ev,
        kind,
        **{
            "mas.governance.hook": ev.get("hook"),
            "mas.governance.denied_call_id": ev.get("denied_call_id"),
        },
    )


@register("budget_event", "control_intervention", "governance_decision")
def h_decision_event(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    _governance_point(
        conv,
        ev,
        str(ev.get("kind") or "governance_decision"),
        **{
            "mas.governance.hook": ev.get("hook"),
            "mas.governance.checkpoint": ev.get("checkpoint"),
        },
    )


_CHECK_INTERVAL_STARTS = (
    "governance_authorize_start",
    "governance_validate_start",
    "obs_wrap_gov_authorize_start",
    "obs_wrap_gov_validate_start",
    "checkpoint_start",
)
_CHECK_INTERVAL_ENDS = tuple(kind[: -len("_start")] + "_end" for kind in _CHECK_INTERVAL_STARTS)


@register(*_CHECK_INTERVAL_STARTS)
def h_governance_interval_start(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    check = str(ev.get("kind") or "governance")[: -len("_start")]
    conv.open_span(
        conv.span_key(ev),
        "ProcessingCall",
        {
            "mas.boundary": "ProcessingCall",
            "mas.agent.id": conv.agent_id(ev),
            "mas.processing.name": check,
            "mas.processing.type": check,
            **({"mas.processing.op": ev["op"]} if ev.get("op") else {}),
            **({"mas.processing.checked_call_id": ev["checked_call_id"]} if ev.get("checked_call_id") else {}),
            **conv.state_start_attrs(ev),
        },
        ev.get("parent_call_id"),
        start_ns=conv.ts_ns(ev),
    )


@register(*_CHECK_INTERVAL_ENDS)
def h_governance_interval_end(conv: SpanEmitter, ev: Dict[str, Any]) -> None:
    key = conv.span_key(ev)
    if _decision(ev):
        _governance_point(conv, ev, str(ev.get("kind") or "governance"), parent=key)
    conv.close_span(
        key,
        conv.state_end_attrs(ev) or None,
        status=ev.get("status", "success"),
        end_ns=conv.ts_ns(ev),
    )
